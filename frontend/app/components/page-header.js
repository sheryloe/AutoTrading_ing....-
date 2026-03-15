import Link from "next/link";

export default function PageHeader({ eyebrow, title, description, actions = [] }) {
  return (
    <header className="page-header">
      <div className="page-header-copy">
        {eyebrow ? <p className="page-eyebrow">{eyebrow}</p> : null}
        <h1 className="page-title">{title}</h1>
        {description ? <p className="page-description">{description}</p> : null}
      </div>
      {actions.length ? (
        <div className="page-actions">
          {actions.map((action) => (
            <Link key={action.href} href={action.href} className={`page-action ${action.tone || "ghost"}`}>
              {action.label}
            </Link>
          ))}
        </div>
      ) : null}
    </header>
  );
}
