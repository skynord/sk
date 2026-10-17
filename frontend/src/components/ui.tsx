import type { ReactNode } from "react";

export function StatCard({
  label,
  value,
  sub,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
}) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub !== undefined && <div className="sub">{sub}</div>}
    </div>
  );
}

export function Card({ title, children }: { title?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      {title !== undefined && <h2>{title}</h2>}
      {children}
    </section>
  );
}

export function Loading({ label = "Loading…", detail }: { label?: string; detail?: string }) {
  return (
    <div className="spinner">
      {label}
      {detail && <div className="muted small" style={{ marginTop: 6 }}>{detail}</div>}
    </div>
  );
}

export function ErrorBox({ message }: { message: string }) {
  return <div className="alert error">{message}</div>;
}

export function InfoBox({ children }: { children: ReactNode }) {
  return <div className="alert info">{children}</div>;
}

export function WarnBox({ children }: { children: ReactNode }) {
  return <div className="alert warn">{children}</div>;
}
