/** Avoid static prerender of the wizard (camera/WebSocket client tree). */
export const dynamic = 'force-dynamic';

export default function ConfigureLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}
