"use client";

import { Printer } from "lucide-react";

import { Button } from "@/components/ui/button";

// Déclenche l'impression navigateur : la page hôte masque tout ce qui n'est
// pas un billet via `print:hidden` (les cartes `.ticket-card` restent).
export function PrintTicketsButton({
  label = "Imprimer les billets",
}: {
  label?: string;
}) {
  return (
    <Button variant="outline" onClick={() => window.print()}>
      <Printer className="size-4" />
      {label}
    </Button>
  );
}
