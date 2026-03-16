export default function MetricCard({ label, value, meta, tone = "default", icon: Icon = null }) {
  return (
    <article className={`metric-card ${tone}`}>
      <div className="metric-head">
        <span className="metric-label">{label}</span>
        {Icon ? <Icon size={16} strokeWidth={2.1} className="metric-icon" aria-hidden="true" /> : null}
      </div>
      <strong className="metric-value">{value}</strong>
      {meta ? <p className="metric-meta">{meta}</p> : null}
    </article>
  );
}
