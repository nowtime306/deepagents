"use client";

export default function ErrorPage({
  retry,
}: {
  error: unknown;
  retry: () => void;
}) {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-5 bg-[#f0f3ee] text-[#355e3a]">
      <h1 className="text-2xl">页面暂时遇到问题</h1>
      <p>请重试，或稍后重新打开工作台。</p>
      <button
        onClick={retry}
        className="rounded-lg border px-5 py-2"
      >
        重新加载
      </button>
    </main>
  );
}
