import type { Metadata } from "next";
import "./globals.css";
import { BiProvider } from "@/lib/context";

export const metadata: Metadata = {
  title: "NexusBI — Enterprise Intelligence Dashboard Platform",
  description: "AI-Powered Enterprise BI, Dynamic Dashboard Builder, RBAC & Automated ETL Warehouse",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" dir="ltr">
      <body>
        <BiProvider>{children}</BiProvider>
      </body>
    </html>
  );
}
