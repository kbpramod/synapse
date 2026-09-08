import { Link, useLocation } from "react-router-dom";
import "./Header.css";

const Header = () => {
  const location = useLocation();

  return (
    <header className="header">
      <div className="header-left">
        <Link to="/" className="brand-logo" title="Forge Home">
          <div className="logo-icon-wrap">
            <svg
              className="logo-icon"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              {/* Modern Forge Flame / Anvil Spark Icon */}
              <path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z" />
            </svg>
          </div>
          <span className="logo-title">Forge</span>
          <span className="logo-badge">AGENT QA</span>
        </Link>

        <nav className="header-nav">
          <Link
            to="/dashboard"
            className={`nav-link ${location.pathname === "/dashboard" ? "active" : ""}`}
          >
            Dashboard
          </Link>
          <Link
            to="/"
            className={`nav-link ${location.pathname === "/" ? "active" : ""}`}
          >
            Register App
          </Link>
        </nav>
      </div>

      <div className="header-right">
        <div className="system-status-indicator" title="Autonomous testing ready">
          <span className="status-dot"></span>
          <span className="status-label">System Ready</span>
        </div>
      </div>
    </header>
  );
};

export default Header;