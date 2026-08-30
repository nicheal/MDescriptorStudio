import { useWorkspace } from "../../stores/workspace";
import { useT } from "../../i18n";

export default function StatusBar() {
  const { backendStatus, engineVersion, runningJobs, cpuThreads } = useWorkspace();
  const { t } = useT();
  const color = backendStatus === "ready" ? "#107C10" : "#C42B1C";
  const threads = cpuThreads ?? (typeof navigator !== "undefined" ? navigator.hardwareConcurrency : null);
  return (
    <div
      style={{
        height: 26,
        borderTop: "1px solid #E1E4E8",
        background: "#FAFAFA",
        display: "flex",
        alignItems: "center",
        padding: "0 12px",
        fontSize: 12,
        color: "#616161",
        gap: 16,
      }}
    >
      <span style={{ display: "inline-flex", alignItems: "center", gap: 6, flex: 1 }}>
        <span
          style={{ width: 8, height: 8, borderRadius: 4, background: color, display: "inline-block" }}
        />
        {runningJobs > 0
          ? runningJobs > 1
            ? t("{n} jobs running", { n: runningJobs })
            : t("{n} job running", { n: runningJobs })
          : t("Ready")}
      </span>
      <span style={{ display: "inline-flex", alignItems: "center" }}>
        MDescriptor {engineVersion ?? "—"}
      </span>
      <span
        style={{ display: "inline-flex", alignItems: "center", justifyContent: "flex-end", flex: 1, gap: 16 }}
      >
        <span style={{ fontVariantNumeric: "tabular-nums" }}>{t("CPU {n} threads", { n: threads ?? "—" })}</span>
      </span>
    </div>
  );
}
