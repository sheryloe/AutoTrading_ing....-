import { IBM_Plex_Mono, Noto_Sans_KR, Space_Grotesk } from "next/font/google";
import AppShell from "./components/app-shell";
import "./globals.css";

const spaceGrotesk = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-display",
});

const notoSansKr = Noto_Sans_KR({
  subsets: ["latin"],
  variable: "--font-body",
  weight: ["400", "500", "700"],
});

const ibmPlexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-mono",
});

export const metadata = {
  title: "AI_Auto 운영자 콘솔",
  description: "트레이딩 자동화 운영 상태, 모델 성과, 포지션, 설정을 분리한 서비스형 콘솔",
};

export default function RootLayout({ children }) {
  return (
    <html lang="ko">
      <body className={`${spaceGrotesk.variable} ${notoSansKr.variable} ${ibmPlexMono.variable}`}>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
