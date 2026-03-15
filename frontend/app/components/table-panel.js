import SectionCard from "./section-card";

export default function TablePanel({ eyebrow, title, meta, children, className = "" }) {
  return (
    <SectionCard eyebrow={eyebrow} title={title} meta={meta} className={className}>
      <div className="table-wrap">{children}</div>
    </SectionCard>
  );
}
