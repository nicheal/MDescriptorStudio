import { useWorkspace } from "../../stores/workspace";

export default function StatusBar() {
  const { backendStatus, engineVersion, runningJobs } = useWorkspace();
  const color = backendStatus === "ready" ? "#107C10" : "#C42B1C";
  return (
    <div
      style={{
        height: 26,
        borderTop: "1px solid #E1E4E8",
        background: "#FAFAFA",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "0 12px",
        fontSize: 12,
        color: "#616161",
      }}
    >
      <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
        <span
          style={{ width: 8, height: 8, borderRadius: 4, background: color, display: "inline-block" }}
        />
        {runningJobs > 0 ? `${runningJobs} job${runningJobs > 1 ? "s" : ""} running` : "Ready"}
      </span>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 16 }}>
        <span>MDescriptor {engineVersion ?? "—"}</span>
        <span style={{ color: "#8A8A8A" }}>Windows x64</span>
      </span>
    </div>
  );
}
