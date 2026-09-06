// Native frame-geometry kernel for dataset statistics (Overview + Data
// Health).  Replaces the per-frame scipy cKDTree work in
// mdescriptor_studio_backend/datasets/statistics.py with one C-ABI call that
// computes the minimum interatomic distance and the short-contact
// (non-physical structure) flag for a single frame.
//
// The semantics replicate the Python reference exactly:
//   * periodic axes are wrapped into the cell (minimum-image convention)
//     before any pair is measured, non-periodic coordinates pass through;
//   * the lattice-image stencil for the minimum distance follows the
//     conservative per-axis bound int(best * |inv col_k|) + 1, capped at
//     image_limit, replicated as the same iterative expansion loop;
//   * the short-contact stencil follows int(t_max * |inv row_k|) + 1;
//   * own-image contacts count for image shifts (a single atom in a periodic
//     cell reports its nearest periodicity length), while the zero-shift scan
//     drops self pairs;
//   * empty or non-finite frames report "no distance" (NaN) and no contact.
//
// Small frames use vectorizable brute-force scans; large frames use a uniform
// grid (cell list) with expanding-ring nearest searches.  Build:
// scripts/build_native.ps1.

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <vector>

#if defined(_WIN32)
#define MDS_EXPORT extern "C" __declspec(dllexport)
#else
#define MDS_EXPORT extern "C" __attribute__((visibility("default")))
#endif

namespace {

constexpr double kInfinity = std::numeric_limits<double>::infinity();
// frames at or below this atom count use brute-force scans
constexpr int64_t kBruteForceLimit = 512;
constexpr int64_t kMaxGridDim = 512;
constexpr int64_t kMaxGridCells = 2 * 1000 * 1000;
constexpr int64_t kRadiiTableLen = 97;

bool all_finite(const double* v, int64_t count) {
    for (int64_t i = 0; i < count; ++i) {
        if (!std::isfinite(v[i])) return false;
    }
    return true;
}

// Row-major 3x3 closed-form inverse; false when the matrix is singular
// (|det| <= tol) or non-finite, matching the periodicity gate in Python.
bool inv3(const double* m, double* inv, double tol) {
    const double m00 = m[0], m01 = m[1], m02 = m[2];
    const double m10 = m[3], m11 = m[4], m12 = m[5];
    const double m20 = m[6], m21 = m[7], m22 = m[8];
    const double det =
        m00 * (m11 * m22 - m12 * m21) - m01 * (m10 * m22 - m12 * m20) +
        m02 * (m10 * m21 - m11 * m20);
    if (!std::isfinite(det) || std::fabs(det) <= tol) return false;
    const double s = 1.0 / det;
    inv[0] = (m11 * m22 - m12 * m21) * s;
    inv[1] = (m02 * m21 - m01 * m22) * s;
    inv[2] = (m01 * m12 - m02 * m11) * s;
    inv[3] = (m12 * m20 - m10 * m22) * s;
    inv[4] = (m00 * m22 - m02 * m20) * s;
    inv[5] = (m02 * m10 - m00 * m12) * s;
    inv[6] = (m10 * m21 - m11 * m20) * s;
    inv[7] = (m01 * m20 - m00 * m21) * s;
    inv[8] = (m00 * m11 - m01 * m10) * s;
    return true;
}

// frac = pos @ inv with periodic axes wrapped into [0, 1), then pts = frac @
// cell — the same minimum-image wrap as _prepared_points in statistics.py.
void wrap_positions(const double* pos, int64_t n, const double* inv,
                    const double* cell, const bool* pbc, double* out) {
    for (int64_t i = 0; i < n; ++i) {
        const double x = pos[3 * i + 0];
        const double y = pos[3 * i + 1];
        const double z = pos[3 * i + 2];
        double f0 = x * inv[0] + y * inv[3] + z * inv[6];
        double f1 = x * inv[1] + y * inv[4] + z * inv[7];
        double f2 = x * inv[2] + y * inv[5] + z * inv[8];
        if (pbc[0]) f0 -= std::floor(f0);
        if (pbc[1]) f1 -= std::floor(f1);
        if (pbc[2]) f2 -= std::floor(f2);
        out[3 * i + 0] = f0 * cell[0] + f1 * cell[3] + f2 * cell[6];
        out[3 * i + 1] = f0 * cell[1] + f1 * cell[4] + f2 * cell[7];
        out[3 * i + 2] = f0 * cell[2] + f1 * cell[5] + f2 * cell[8];
    }
}

// ---------------------------------------------------------------- brute force

double core_min_bf(const double* p, int64_t n) {
    double best2 = kInfinity;
    for (int64_t i = 0; i < n; ++i) {
        const double ax = p[3 * i + 0];
        const double ay = p[3 * i + 1];
        const double az = p[3 * i + 2];
        for (int64_t j = i + 1; j < n; ++j) {
            const double dx = ax - p[3 * j + 0];
            const double dy = ay - p[3 * j + 1];
            const double dz = az - p[3 * j + 2];
            const double d2 = dx * dx + dy * dy + dz * dz;
            if (d2 < best2) best2 = d2;
        }
    }
    return std::sqrt(best2);
}

// Minimum distance between the point set and its lattice-image copies, own-
// image contacts included (the i == j diagonal contributes |shift|).  `bound`
// carries the running global minimum; the result never reports above it.
// Shifts arrive as structure-of-arrays so the inner shift loop vectorizes,
// with each pair's displacement loaded once for all lattice images.
double shift_min_bf(const double* p, int64_t n, const double* sx,
                    const double* sy, const double* sz, int64_t n_shifts,
                    double bound) {
    double best2 = kInfinity;
    for (int64_t k = 0; k < n_shifts; ++k) {
        const double d2 = sx[k] * sx[k] + sy[k] * sy[k] + sz[k] * sz[k];
        if (d2 < best2) best2 = d2;
    }
    if (bound * bound < best2) best2 = bound * bound;
    for (int64_t i = 0; i < n; ++i) {
        const double ax = p[3 * i + 0];
        const double ay = p[3 * i + 1];
        const double az = p[3 * i + 2];
        for (int64_t j = 0; j < n; ++j) {
            if (j == i) continue;
            const double dx = ax - p[3 * j + 0];
            const double dy = ay - p[3 * j + 1];
            const double dz = az - p[3 * j + 2];
            for (int64_t k = 0; k < n_shifts; ++k) {
                const double ex = dx - sx[k];
                const double ey = dy - sy[k];
                const double ez = dz - sz[k];
                const double d2 = ex * ex + ey * ey + ez * ez;
                if (d2 < best2) best2 = d2;
            }
        }
    }
    return std::sqrt(best2);
}

bool short_contact_bf(const double* p, int64_t n, const double* radii,
                      double coef) {
    for (int64_t i = 0; i < n; ++i) {
        const double ax = p[3 * i + 0];
        const double ay = p[3 * i + 1];
        const double az = p[3 * i + 2];
        const double ri = radii[i];
        for (int64_t j = i + 1; j < n; ++j) {
            const double dx = ax - p[3 * j + 0];
            const double dy = ay - p[3 * j + 1];
            const double dz = az - p[3 * j + 2];
            const double bound = coef * (ri + radii[j]);
            const double d2 = dx * dx + dy * dy + dz * dz;
            if (d2 < bound * bound) return true;
        }
    }
    return false;
}

// Image-shift scan: the shifted copy of atom i (center) against every atom j,
// own contacts (j == i) included, exactly like the Python ball-query pass.
bool short_contact_shift_bf(const double* p, int64_t n, const double* radii,
                            double coef, double sx, double sy, double sz) {
    for (int64_t i = 0; i < n; ++i) {
        const double ax = p[3 * i + 0] + sx;
        const double ay = p[3 * i + 1] + sy;
        const double az = p[3 * i + 2] + sz;
        const double ri = radii[i];
        for (int64_t j = 0; j < n; ++j) {
            const double dx = ax - p[3 * j + 0];
            const double dy = ay - p[3 * j + 1];
            const double dz = az - p[3 * j + 2];
            const double bound = coef * (ri + radii[j]);
            const double d2 = dx * dx + dy * dy + dz * dz;
            if (d2 < bound * bound) return true;
        }
    }
    return false;
}

// ----------------------------------------------------------------- point grid

// Uniform grid over an axis-aligned bounding box; points are indexed by cell
// in CSR layout.  Used for frames above kBruteForceLimit so per-frame cost
// stays near-linear in the atom count.
struct Grid {
    double lo[3];
    double width[3];  // per-axis cell width
    double hmin;
    int64_t dims[3];
    std::vector<int64_t> start;  // cells + 1 entries
    std::vector<int64_t> items;  // point indices grouped by cell

    int64_t cells() const { return dims[0] * dims[1] * dims[2]; }
};

// clamped cell coordinate of a (query) point
inline void grid_coord(const Grid& g, double x, double y, double z,
                       int64_t out[3]) {
    const double q[3] = {x, y, z};
    for (int k = 0; k < 3; ++k) {
        double v = std::floor((q[k] - g.lo[k]) / g.width[k]);
        if (!(v > 0.0)) v = 0.0;  // also catches NaN
        const double top = static_cast<double>(g.dims[k] - 1);
        if (v > top) v = top;
        out[k] = static_cast<int64_t>(v);
    }
}

inline int64_t cell_index_of(const Grid& g, const int64_t c[3]) {
    return (c[0] * g.dims[1] + c[1]) * g.dims[2] + c[2];
}

void build_grid(const double* p, int64_t n, const double lo[3],
                const double hi[3], Grid& g) {
    double ext[3];
    for (int k = 0; k < 3; ++k) ext[k] = std::max(hi[k] - lo[k], 1e-12);
    const double volume = ext[0] * ext[1] * ext[2];
    // target occupancy ~3 points per cell: cells ~ n/3 regardless of the box
    double h = std::cbrt(3.0 * volume / static_cast<double>(n));
    if (!std::isfinite(h) || h <= 0.0) {
        h = std::max(ext[0], std::max(ext[1], ext[2]));
    }
    int64_t dims[3] = {1, 1, 1};
    for (int attempt = 0; attempt < 64; ++attempt) {
        for (int k = 0; k < 3; ++k) {
            const double v = std::ceil(ext[k] / h);
            int64_t d = (v < 1.0) ? 1 : static_cast<int64_t>(v);
            if (d > kMaxGridDim) d = kMaxGridDim;
            dims[k] = d;
        }
        const int64_t cells = dims[0] * dims[1] * dims[2];
        if (cells <= kMaxGridCells || h > 1e100) break;
        h *= 1.3;
    }
    g.lo[0] = lo[0]; g.lo[1] = lo[1]; g.lo[2] = lo[2];
    g.dims[0] = dims[0]; g.dims[1] = dims[1]; g.dims[2] = dims[2];
    g.hmin = kInfinity;
    for (int k = 0; k < 3; ++k) {
        g.width[k] = ext[k] / static_cast<double>(dims[k]);
        if (g.width[k] < g.hmin) g.hmin = g.width[k];
    }
    const int64_t cells = g.cells();
    g.start.assign(static_cast<size_t>(cells) + 1, 0);
    g.items.assign(static_cast<size_t>(n), -1);
    int64_t coord[3];
    for (int64_t i = 0; i < n; ++i) {
        grid_coord(g, p[3 * i + 0], p[3 * i + 1], p[3 * i + 2], coord);
        g.start[static_cast<size_t>(cell_index_of(g, coord)) + 1] += 1;
    }
    for (int64_t c = 0; c < cells; ++c) {
        g.start[static_cast<size_t>(c) + 1] += g.start[static_cast<size_t>(c)];
    }
    std::vector<int64_t> cursor(g.start.begin(), g.start.end() - 1);
    for (int64_t i = 0; i < n; ++i) {
        grid_coord(g, p[3 * i + 0], p[3 * i + 1], p[3 * i + 2], coord);
        const size_t cell = static_cast<size_t>(cell_index_of(g, coord));
        g.items[static_cast<size_t>(cursor[cell]++)] = i;
    }
}

// Nearest-point squared distance for one query point.  Scans Chebyshev rings
// outward and stops once further rings cannot beat `bound2`; `exclude` skips a
// single point index (the query itself for core scans, -1 for none).
double nn_query2(const Grid& g, const double* p, double qx, double qy,
                 double qz, int64_t exclude, double bound2) {
    int64_t c[3];
    grid_coord(g, qx, qy, qz, c);
    double best2 = bound2;
    const int64_t max_ring =
        std::max(g.dims[0], std::max(g.dims[1], g.dims[2]));
    for (int64_t r = 0; r <= max_ring; ++r) {
        const int64_t lo0 = std::max(c[0] - r, static_cast<int64_t>(0));
        const int64_t hi0 = std::min(c[0] + r, g.dims[0] - 1);
        const int64_t lo1 = std::max(c[1] - r, static_cast<int64_t>(0));
        const int64_t hi1 = std::min(c[1] + r, g.dims[1] - 1);
        const int64_t lo2 = std::max(c[2] - r, static_cast<int64_t>(0));
        const int64_t hi2 = std::min(c[2] + r, g.dims[2] - 1);
        for (int64_t a = lo0; a <= hi0; ++a) {
            for (int64_t b = lo1; b <= hi1; ++b) {
                for (int64_t d = lo2; d <= hi2; ++d) {
                    // Chebyshev ring r only (ring 0 is the query cell itself)
                    const int64_t da = std::abs(a - c[0]);
                    const int64_t db = std::abs(b - c[1]);
                    const int64_t dd = std::abs(d - c[2]);
                    if (std::max(da, std::max(db, dd)) != r) continue;
                    const size_t cell =
                        static_cast<size_t>((a * g.dims[1] + b) * g.dims[2] + d);
                    const int64_t stop = g.start[cell + 1];
                    for (int64_t t = g.start[cell]; t < stop; ++t) {
                        const int64_t idx = g.items[static_cast<size_t>(t)];
                        if (idx == exclude) continue;
                        const double dx = qx - p[3 * idx + 0];
                        const double dy = qy - p[3 * idx + 1];
                        const double dz = qz - p[3 * idx + 2];
                        const double d2 = dx * dx + dy * dy + dz * dz;
                        if (d2 < best2) best2 = d2;
                    }
                }
            }
        }
        // points beyond ring r sit at Cartesian distance >= r * hmin
        const double reach = static_cast<double>(r) * g.hmin;
        if (reach * reach >= best2) break;
    }
    return best2;
}

double core_min_grid(const Grid& g, const double* p, int64_t n) {
    double best2 = kInfinity;
    for (int64_t i = 0; i < n; ++i) {
        const double d2 =
            nn_query2(g, p, p[3 * i + 0], p[3 * i + 1], p[3 * i + 2], i, best2);
        if (d2 < best2) best2 = d2;
    }
    return std::sqrt(best2);
}

double shift_min_grid(const Grid& g, const double* p, int64_t n, double sx,
                      double sy, double sz, double bound) {
    double best2 = sx * sx + sy * sy + sz * sz;  // own-image (i == j) contacts
    if (bound < std::sqrt(best2)) best2 = bound * bound;
    for (int64_t i = 0; i < n; ++i) {
        const double d2 =
            nn_query2(g, p, p[3 * i + 0] + sx, p[3 * i + 1] + sy,
                      p[3 * i + 2] + sz, -1, best2);
        if (d2 < best2) best2 = d2;
    }
    return std::sqrt(best2);
}

// Ball scan for the short-contact predicate around one shifted atom center.
// `exclude` drops the pair partner identical to the center atom for the
// zero-shift scan (-1 keeps own-image contacts for image shifts).
bool ball_hits(const Grid& g, const double* p, const double* radii, double qx,
               double qy, double qz, double ri, double coef, double t_max,
               int64_t exclude) {
    int64_t c[3];
    grid_coord(g, qx, qy, qz, c);
    int64_t reach[3];
    for (int k = 0; k < 3; ++k) {
        const double v = std::floor(t_max / g.width[k]) + 1.0;
        int64_t r = (v < 1.0) ? 1 : static_cast<int64_t>(v);
        if (r > g.dims[k]) r = g.dims[k];
        reach[k] = r;
    }
    const int64_t lo0 = std::max(c[0] - reach[0], static_cast<int64_t>(0));
    const int64_t hi0 = std::min(c[0] + reach[0], g.dims[0] - 1);
    const int64_t lo1 = std::max(c[1] - reach[1], static_cast<int64_t>(0));
    const int64_t hi1 = std::min(c[1] + reach[1], g.dims[1] - 1);
    const int64_t lo2 = std::max(c[2] - reach[2], static_cast<int64_t>(0));
    const int64_t hi2 = std::min(c[2] + reach[2], g.dims[2] - 1);
    for (int64_t a = lo0; a <= hi0; ++a) {
        for (int64_t b = lo1; b <= hi1; ++b) {
            for (int64_t d = lo2; d <= hi2; ++d) {
                const size_t cell =
                    static_cast<size_t>((a * g.dims[1] + b) * g.dims[2] + d);
                const int64_t stop = g.start[cell + 1];
                for (int64_t t = g.start[cell]; t < stop; ++t) {
                    const int64_t idx = g.items[static_cast<size_t>(t)];
                    if (idx == exclude) continue;
                    const double dx = qx - p[3 * idx + 0];
                    const double dy = qy - p[3 * idx + 1];
                    const double dz = qz - p[3 * idx + 2];
                    const double bound = coef * (ri + radii[idx]);
                    const double d2 = dx * dx + dy * dy + dz * dz;
                    if (d2 < bound * bound) return true;
                }
            }
        }
    }
    return false;
}

bool short_contact_grid_zero(const Grid& g, const double* p,
                             const double* radii, int64_t n, double coef,
                             double t_max) {
    for (int64_t i = 0; i < n; ++i) {
        if (ball_hits(g, p, radii, p[3 * i + 0], p[3 * i + 1], p[3 * i + 2],
                      radii[i], coef, t_max, i)) {
            return true;
        }
    }
    return false;
}

bool short_contact_grid_shift(const Grid& g, const double* p,
                              const double* radii, int64_t n, double coef,
                              double t_max, double sx, double sy, double sz) {
    for (int64_t i = 0; i < n; ++i) {
        if (ball_hits(g, p, radii, p[3 * i + 0] + sx, p[3 * i + 1] + sy,
                      p[3 * i + 2] + sz, radii[i], coef, t_max, -1)) {
            return true;
        }
    }
    return false;
}

// ------------------------------------------------------------ stencil helpers

void shift_vector(const double* cell, int64_t s0, int64_t s1, int64_t s2,
                  double out[3]) {
    out[0] = s0 * cell[0] + s1 * cell[3] + s2 * cell[6];
    out[1] = s0 * cell[1] + s1 * cell[4] + s2 * cell[7];
    out[2] = s0 * cell[2] + s1 * cell[5] + s2 * cell[8];
}

void bbox_of(const double* p, int64_t n, double lo[3], double hi[3]) {
    lo[0] = lo[1] = lo[2] = kInfinity;
    hi[0] = hi[1] = hi[2] = -kInfinity;
    for (int64_t i = 0; i < n; ++i) {
        for (int k = 0; k < 3; ++k) {
            const double v = p[3 * i + k];
            if (v < lo[k]) lo[k] = v;
            if (v > hi[k]) hi[k] = v;
        }
    }
}

}  // namespace

// Computes the frame minimum interatomic distance and the short-contact flag
// in one pass.  Returns 0 on success (results in the out params; NaN means
// "no distance"), nonzero on invalid arguments.
MDS_EXPORT int mds_frame_geometry(
    const double* positions, const int64_t* numbers, const double* cell,
    const unsigned char* pbc, const double* radii_table, int64_t n,
    double short_contact_coefficient, double cell_det_tol, int64_t image_limit,
    double* out_min_distance, unsigned char* out_short_contact) {
    if (out_min_distance == nullptr || out_short_contact == nullptr) return 2;
    *out_min_distance = std::numeric_limits<double>::quiet_NaN();
    *out_short_contact = 0;
    if (n < 0 || positions == nullptr || numbers == nullptr || cell == nullptr ||
        pbc == nullptr || radii_table == nullptr) {
        return 2;
    }
    if (n == 0) return 0;  // no atoms: no distance, no contact
    if (!all_finite(positions, 3 * n)) return 0;  // invalid coordinates
    if (!(short_contact_coefficient >= 0.0) || image_limit < 0) return 2;

    const bool pbc_flag[3] = {pbc[0] != 0, pbc[1] != 0, pbc[2] != 0};
    const bool any_pbc = pbc_flag[0] || pbc_flag[1] || pbc_flag[2];
    double inv[9] = {0};
    bool periodic = false;
    if (any_pbc && all_finite(cell, 9)) {
        periodic = inv3(cell, inv, cell_det_tol);
    }

    // per-atom covalent radii with the same clamp as radii_for()
    std::vector<double> radii(static_cast<size_t>(n));
    for (int64_t i = 0; i < n; ++i) {
        int64_t z = numbers[i];
        if (z < 1) z = 1;
        if (z > kRadiiTableLen - 1) z = kRadiiTableLen - 1;
        radii[static_cast<size_t>(i)] = radii_table[static_cast<size_t>(z)];
    }

    std::vector<double> pts(static_cast<size_t>(3 * n));
    const double* p = pts.data();
    if (periodic) {
        wrap_positions(positions, n, inv, cell, pbc_flag, pts.data());
    } else {
        std::memcpy(pts.data(), positions,
                    sizeof(double) * 3 * static_cast<size_t>(n));
    }

    // ---- minimum interatomic distance ------------------------------------
    double best = kInfinity;
    Grid core_grid;
    bool have_core_grid = false;
    double core_lo[3] = {0, 0, 0};
    double core_hi[3] = {0, 0, 0};
    if (n >= 2) {
        if (n <= kBruteForceLimit) {
            best = core_min_bf(p, n);
        } else {
            bbox_of(p, n, core_lo, core_hi);
            build_grid(p, n, core_lo, core_hi, core_grid);
            have_core_grid = true;
            best = core_min_grid(core_grid, p, n);
        }
    } else if (!periodic) {
        return 0;  // single isolated atom: no pairs at all
    }

    if (periodic) {
        // lattice-image stencil: the same iterative expansion as the Python
        // loop.  For n >= 2 `best` is finite entering the first round, so the
        // limits are final there and one query round suffices; the single
        // periodic atom case (best == inf) may expand once more.
        int64_t limits[3] = {pbc_flag[0] ? 1 : 0, pbc_flag[1] ? 1 : 0,
                             pbc_flag[2] ? 1 : 0};
        const bool use_image_grid = have_core_grid && n > kBruteForceLimit;
        std::vector<std::array<int64_t, 3>> seen;
        Grid image_grid;
        bool image_grid_ready = false;
        while (true) {
            if (std::isfinite(best)) {
                for (int k = 0; k < 3; ++k) {
                    if (!pbc_flag[k]) continue;
                    // column k of inv (row-major): inv[3r + k]
                    const double col = std::sqrt(inv[k] * inv[k] +
                                                 inv[3 + k] * inv[3 + k] +
                                                 inv[6 + k] * inv[6 + k]);
                    int64_t want =
                        static_cast<int64_t>(std::floor(best * col)) + 1;
                    if (want > image_limit) want = image_limit;
                    if (want > limits[k]) limits[k] = want;
                }
            }
            std::vector<std::array<int64_t, 3>> round_tuples;
            for (int64_t s0 = -limits[0]; s0 <= limits[0]; ++s0) {
                for (int64_t s1 = -limits[1]; s1 <= limits[1]; ++s1) {
                    for (int64_t s2 = -limits[2]; s2 <= limits[2]; ++s2) {
                        if (s0 == 0 && s1 == 0 && s2 == 0) continue;
                        const std::array<int64_t, 3> t = {s0, s1, s2};
                        if (std::find(seen.begin(), seen.end(), t) == seen.end()) {
                            round_tuples.push_back(t);
                        }
                    }
                }
            }
            if (round_tuples.empty()) break;
            seen.insert(seen.end(), round_tuples.begin(), round_tuples.end());
            std::vector<double> sx(round_tuples.size()), sy(round_tuples.size()),
                sz(round_tuples.size());
            for (size_t t = 0; t < round_tuples.size(); ++t) {
                double s[3];
                shift_vector(cell, round_tuples[t][0], round_tuples[t][1],
                             round_tuples[t][2], s);
                sx[t] = s[0]; sy[t] = s[1]; sz[t] = s[2];
            }
            if (use_image_grid && !image_grid_ready) {
                // the grid must cover every shifted query: core box union the
                // shift vectors (limits are final here because best is finite)
                double ulo[3], uhi[3];
                for (int k = 0; k < 3; ++k) {
                    ulo[k] = core_lo[k];
                    uhi[k] = core_hi[k];
                }
                for (size_t t = 0; t < round_tuples.size(); ++t) {
                    const double comp[3] = {sx[t], sy[t], sz[t]};
                    for (int k = 0; k < 3; ++k) {
                        ulo[k] = std::min(ulo[k], core_lo[k] + comp[k]);
                        uhi[k] = std::max(uhi[k], core_hi[k] + comp[k]);
                    }
                }
                build_grid(p, n, ulo, uhi, image_grid);
                image_grid_ready = true;
            }
            if (image_grid_ready) {
                for (size_t t = 0; t < round_tuples.size(); ++t) {
                    const double shift_best = shift_min_grid(
                        image_grid, p, n, sx[t], sy[t], sz[t], best);
                    if (shift_best < best) best = shift_best;
                }
            } else {
                const double shift_best =
                    shift_min_bf(p, n, sx.data(), sy.data(), sz.data(),
                                 static_cast<int64_t>(round_tuples.size()), best);
                if (shift_best < best) best = shift_best;
            }
        }
    }

    if (!std::isfinite(best)) return 0;  // defensive; unreachable in practice
    *out_min_distance = best;

    // ---- short-contact (non-physical structure) check ---------------------
    double r_max = 0.0;
    for (int64_t i = 0; i < n; ++i) {
        if (radii[static_cast<size_t>(i)] > r_max) {
            r_max = radii[static_cast<size_t>(i)];
        }
    }
    const double t_max = 2.0 * short_contact_coefficient * r_max;
    if (!(t_max > 0.0) || best >= t_max) return 0;  // early exit as in Python

    if (n <= kBruteForceLimit) {
        if (short_contact_bf(p, n, radii.data(), short_contact_coefficient)) {
            *out_short_contact = 1;
        }
    } else if (short_contact_grid_zero(core_grid, p, radii.data(), n,
                                       short_contact_coefficient, t_max)) {
        *out_short_contact = 1;
    }
    if (*out_short_contact != 0 || !periodic) return 0;

    // image shifts (row-norm bound, capped, zero shift excluded)
    int64_t limits[3] = {0, 0, 0};
    for (int k = 0; k < 3; ++k) {
        if (!pbc_flag[k]) continue;
        // row k of inv (row-major): inv[3k + c]
        const double row = std::sqrt(inv[3 * k + 0] * inv[3 * k + 0] +
                                     inv[3 * k + 1] * inv[3 * k + 1] +
                                     inv[3 * k + 2] * inv[3 * k + 2]);
        int64_t want = static_cast<int64_t>(std::floor(t_max * row)) + 1;
        if (want > image_limit) want = image_limit;
        limits[k] = want;
    }
    if (limits[0] == 0 && limits[1] == 0 && limits[2] == 0) return 0;

    if (n <= kBruteForceLimit) {
        for (int64_t s0 = -limits[0]; s0 <= limits[0]; ++s0) {
            for (int64_t s1 = -limits[1]; s1 <= limits[1]; ++s1) {
                for (int64_t s2 = -limits[2]; s2 <= limits[2]; ++s2) {
                    if (s0 == 0 && s1 == 0 && s2 == 0) continue;
                    double s[3];
                    shift_vector(cell, s0, s1, s2, s);
                    if (short_contact_shift_bf(p, n, radii.data(),
                                               short_contact_coefficient, s[0],
                                               s[1], s[2])) {
                        *out_short_contact = 1;
                        return 0;
                    }
                }
            }
        }
        return 0;
    }

    // grid path: one grid over the union of the core box and every shift of
    // the short-contact stencil
    double slo[3] = {kInfinity, kInfinity, kInfinity};
    double shi[3] = {-kInfinity, -kInfinity, -kInfinity};
    for (int64_t s0 = -limits[0]; s0 <= limits[0]; ++s0) {
        for (int64_t s1 = -limits[1]; s1 <= limits[1]; ++s1) {
            for (int64_t s2 = -limits[2]; s2 <= limits[2]; ++s2) {
                if (s0 == 0 && s1 == 0 && s2 == 0) continue;
                double s[3];
                shift_vector(cell, s0, s1, s2, s);
                for (int k = 0; k < 3; ++k) {
                    if (s[k] < slo[k]) slo[k] = s[k];
                    if (s[k] > shi[k]) shi[k] = s[k];
                }
            }
        }
    }
    double ulo[3], uhi[3];
    for (int k = 0; k < 3; ++k) {
        ulo[k] = core_lo[k] + slo[k];
        uhi[k] = core_hi[k] + shi[k];
    }
    Grid image_grid;
    build_grid(p, n, ulo, uhi, image_grid);
    for (int64_t s0 = -limits[0]; s0 <= limits[0]; ++s0) {
        for (int64_t s1 = -limits[1]; s1 <= limits[1]; ++s1) {
            for (int64_t s2 = -limits[2]; s2 <= limits[2]; ++s2) {
                if (s0 == 0 && s1 == 0 && s2 == 0) continue;
                double s[3];
                shift_vector(cell, s0, s1, s2, s);
                if (short_contact_grid_shift(image_grid, p, radii.data(), n,
                                             short_contact_coefficient, t_max,
                                             s[0], s[1], s[2])) {
                    *out_short_contact = 1;
                    return 0;
                }
            }
        }
    }
    return 0;
}
