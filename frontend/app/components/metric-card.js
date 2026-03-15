export default function MetricCard({ label, value, meta, tone = "default" }) {
  return (
    <article className={`metric-card ${tone}`}>
      <span className="metric-label">{label}</span>
      <strong className="metric-value">{value}</strong>
      {meta ? <p className="metric-meta">{meta}</p> : null}
    </article>
  );
}
