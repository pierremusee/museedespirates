"use client";

import { QRCodeSVG } from "qrcode.react";

export function TicketQR({ value }: { value: string }) {
  return <QRCodeSVG value={value} size={160} level="M" />;
}
