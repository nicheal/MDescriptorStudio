/** Scientific plots are Plotly by convention.
 *
 * This module is the only import Analysis code should use when it needs a
 * scientific canvas. Swapping the underlying renderer later (or lazy-loading
 * it) is then one change in the visualization layer instead of a page edit.
 */
export { default } from "../plotlyBundle";
