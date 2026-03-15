export default function StatusBadge({ tone = "default", children }) {
  return <span className={`status-badge status-${tone}`}>{children}</span>;
}
