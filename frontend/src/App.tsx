import { Routes, Route, NavLink, Navigate, useLocation } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import Matches from "./pages/Matches";
import MatchAnalysis from "./pages/MatchAnalysis";
import Predictions from "./pages/Predictions";
import Ask from "./pages/Ask";

type IconProps = { className?: string };

function IconGrid({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7.5" height="7.5" rx="2" />
      <rect x="13.5" y="3" width="7.5" height="7.5" rx="2" />
      <rect x="3" y="13.5" width="7.5" height="7.5" rx="2" />
      <rect x="13.5" y="13.5" width="7.5" height="7.5" rx="2" />
    </svg>
  );
}

function IconList({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 6h13M8 12h13M8 18h13" />
      <circle cx="3.5" cy="6" r="1.3" fill="currentColor" stroke="none" />
      <circle cx="3.5" cy="12" r="1.3" fill="currentColor" stroke="none" />
      <circle cx="3.5" cy="18" r="1.3" fill="currentColor" stroke="none" />
    </svg>
  );
}

function IconSpark({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 3v4M12 17v4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M3 12h4M17 12h4M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8" />
    </svg>
  );
}

function IconChat({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 9.4 9.4 0 0 1-2.9-.4L4 21l1.4-3.6A8.3 8.3 0 0 1 3 11.5 8.4 8.4 0 0 1 12 3a8.4 8.4 0 0 1 9 8.5Z" />
      <path d="M8.4 11.5h.01M12 11.5h.01M15.6 11.5h.01" />
    </svg>
  );
}

const NAV = [
  { to: "/", label: "Dashboard", title: "Dashboard", Icon: IconGrid, end: true },
  { to: "/matches", label: "Matches", title: "Matches", Icon: IconList, end: false },
  { to: "/predictions", label: "Predictions", title: "Predictions", Icon: IconSpark, end: false },
  { to: "/chat", label: "Ask Sky", title: "Ask Sky", Icon: IconChat, end: false },
];

function titleFor(pathname: string): string {
  if (pathname.startsWith("/matches/")) return "Match analysis";
  const hit = NAV.find((n) => (n.end ? pathname === n.to : pathname.startsWith(n.to)));
  return hit?.title ?? "Sky";
}

function Layout({ children }: { children: React.ReactNode }) {
  const { pathname } = useLocation();
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          Sky
        </div>
        <div className="brand-sub">football predictions &amp; analysis</div>
        <nav className="nav">
          {NAV.map(({ to, label, Icon, end }) => (
            <NavLink key={to} to={to} end={end}>
              <Icon />
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="main">
        <div className="topbar">
          <span className="live-dot" />
          <span className="topbar-title">{titleFor(pathname)}</span>
        </div>
        <div key={pathname} className="page-enter">
          {children}
        </div>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/matches" element={<Matches />} />
        <Route path="/matches/:id" element={<MatchAnalysis />} />
        <Route path="/predictions" element={<Predictions />} />
        <Route path="/chat" element={<Ask />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}
