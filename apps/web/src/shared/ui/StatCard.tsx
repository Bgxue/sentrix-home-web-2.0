/** 统计卡。对应 1.0 的 `.stat-card`（`styles.css:105-109`）。 */

interface Props {
  readonly label: string;
  readonly value: string | number;
  readonly note: string;
  readonly accent?: boolean;
}

export function StatCard({ label, value, note, accent = false }: Props) {
  return (
    <article className={accent ? "stat-card accent" : "stat-card"}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </article>
  );
}
