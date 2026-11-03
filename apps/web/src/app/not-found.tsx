import Link from "next/link";

export default function NotFound() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-5 bg-[#f0f3ee] text-[#355e3a]">
      <p className="text-sm">404</p>
      <h1 className="text-2xl">页面未找到</h1>
      <p>页面可能已移动，请返回工作台继续。</p>
      <Link
        href="/"
        className="underline"
      >
        返回工作台
      </Link>
    </main>
  );
}
