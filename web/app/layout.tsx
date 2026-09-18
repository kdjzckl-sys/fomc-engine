import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "FOMC · reaction function",
  description:
    "An ordered-logit model of the Federal Reserve's reaction function, fitted on 36 years of decisions with point-in-time data and scored walk-forward against its baselines.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
