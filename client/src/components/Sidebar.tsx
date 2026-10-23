"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const navItems = [
  {
    href: "/knowledge-base",
    label: "Knowledge Base",
    children: [
      { href: "/knowledge-base/upload", label: "Upload manuals" },
      { href: "/knowledge-base/browse", label: "Browse nodes" },
      { href: "/knowledge-base/cross-references", label: "Cross-references" },
      { href: "/knowledge-base/graph", label: "Graph" },
    ],
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-5 w-5">
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.04A8.97 8.97 0 0 0 6 3.75c-1.05 0-2.06.18-3 .51v14.25A8.99 8.99 0 0 1 6 18c2.3 0 4.4.87 6 2.29m0-14.25a8.97 8.97 0 0 1 6-2.29c1.05 0 2.06.18 3 .51v14.25A8.99 8.99 0 0 0 18 18a8.97 8.97 0 0 0-6 2.29m0-14.25v14.25" />
      </svg>
    ),
  },
  {
    href: "/chat",
    label: "Chat",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-5 w-5">
        <path strokeLinecap="round" strokeLinejoin="round" d="M8.6 20.25c-.6 0-1.18-.06-1.74-.17A5.24 5.24 0 0 1 3.75 21a5.23 5.23 0 0 0 .94-2.88A7.94 7.94 0 0 1 2.25 12.6c0-4.6 4.03-8.35 9-8.35s9 3.74 9 8.35-4.03 8.35-9 8.35c-.92 0-1.8-.13-2.65-.35ZM8.25 12h.01M12 12h.01M15.75 12h.01" />
      </svg>
    ),
  },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-line bg-mint">
      <div className="flex items-center gap-3 px-6 py-6">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-principal text-lg font-bold text-lime shadow-md shadow-principal/30">
          CE
        </div>
        <div>
          <p className="text-base font-semibold text-principal-deep">Civil Eng</p>
          <p className="text-xs text-muted">Workspace</p>
        </div>
      </div>

      <nav className="flex flex-col gap-1 px-3">
        {navItems.map((item) => {
          const active = pathname.startsWith(item.href);
          const link = (
            <Link
              href={item.href}
              className={`flex items-center gap-3 rounded-xl px-4 py-3 text-sm font-medium transition-all ${
                active
                  ? "bg-principal text-blanco shadow-md shadow-principal/25"
                  : "text-texto hover:bg-lime/60 hover:text-principal-deep"
              }`}
            >
              {item.icon}
              {item.label}
            </Link>
          );
          const children = item.children && active && (
            <div className="mb-1 ml-6 mt-1 flex flex-col border-l-2 border-line pl-3">
              {item.children.map((child) => {
                const childActive = pathname === child.href;
                return (
                  <Link
                    key={child.href}
                    href={child.href}
                    className={`rounded-lg px-3 py-2 text-sm transition ${
                      childActive
                        ? "bg-blanco font-medium text-principal shadow-sm"
                        : "text-muted hover:bg-lime/50 hover:text-principal-deep"
                    }`}
                  >
                    {child.label}
                  </Link>
                );
              })}
            </div>
          );
          return (
            <div key={item.href}>
              {link}
              {children}
            </div>
          );
        })}
      </nav>

      <div className="mt-auto p-4">
        <div className="rounded-xl border border-line bg-blanco p-4 text-xs text-texto">
          <p className="font-semibold">Need help?</p>
          <p className="mt-1 text-muted">Browse the standards or ask in chat.</p>
        </div>
      </div>
    </aside>
  );
}
