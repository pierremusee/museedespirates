import { TicketQR } from "./ticket-qr";

// Billet au format carte bancaire (CR80, 85,6 × 54 mm) : QR + catégorie +
// accès réservés avec dates + référence de la commande. Partagé entre le
// site public (/succes) et la caisse ; l'impression se fait via window.print()
// et les utilitaires `print:hidden` des pages hôtes.

export type TicketCardAccess = {
  id: string;
  access_type: string;
  session_start: string | null;
  session_event_title: string | null;
  valid_date: string | null;
  is_scanned: boolean;
};

export type TicketCardTicket = {
  id: string;
  ticket_category: string;
  accesses: TicketCardAccess[];
};

const CATEGORY_LABELS: Record<string, string> = {
  adult: "Adulte",
  child: "Enfant",
  reduced: "Tarif réduit",
  group: "Groupe",
  school: "Scolaire",
};

const ACCESS_TYPE_LABELS: Record<string, string> = {
  open_ticket: "Musée",
  session_standard: "Théâtre",
  session_dining: "Dîner-spectacle",
};

const dateTimeFormatter = new Intl.DateTimeFormat("fr-FR", {
  dateStyle: "short",
  timeStyle: "short",
  timeZone: "Europe/Paris",
});

function accessDate(a: TicketCardAccess): string {
  if (a.session_start)
    return dateTimeFormatter.format(new Date(a.session_start));
  if (a.valid_date)
    return new Date(`${a.valid_date}T12:00:00`).toLocaleDateString("fr-FR");
  return "";
}

// Référence client courte et lisible : 8 premiers caractères hex de l'UUID
// de la commande (ex. « 3F2A-9B1C »).
function shortRef(id: string): string {
  const hex = id.replace(/-/g, "").slice(0, 8).toUpperCase();
  return `${hex.slice(0, 4)}-${hex.slice(4)}`;
}

export function TicketCard({
  ticket,
  reservationRef,
}: {
  ticket: TicketCardTicket;
  reservationRef?: string | null;
}) {
  return (
    <div className="ticket-card flex h-[54mm] w-[85.6mm] shrink-0 break-inside-avoid overflow-hidden rounded-xl border bg-card text-card-foreground shadow-sm print:shadow-none">
      <div className="flex w-[30mm] shrink-0 flex-col items-center justify-center gap-1 border-r border-dashed p-1.5">
        <div className="rounded-md bg-white p-1">
          <TicketQR value={ticket.id} size={84} />
        </div>
        <p className="font-mono text-[8px] leading-none text-muted-foreground">
          {ticket.id.slice(0, 8)}
        </p>
      </div>
      <div className="flex min-w-0 flex-1 flex-col px-2 py-1.5">
        <p className="text-[9px] font-bold uppercase tracking-widest text-muted-foreground">
          Musée des Pirates
        </p>
        <p className="text-[13px] font-bold leading-tight">
          {CATEGORY_LABELS[ticket.ticket_category] ?? ticket.ticket_category}
        </p>
        <ul className="mt-1 flex-1 space-y-0.5 text-[9px] leading-snug">
          {ticket.accesses.map((a) => (
            <li key={a.id}>
              <span className="font-medium">
                {ACCESS_TYPE_LABELS[a.access_type] ?? a.access_type}
              </span>
              {a.session_event_title ? ` — ${a.session_event_title}` : ""}
              {accessDate(a) && (
                <span className="text-muted-foreground">
                  {" "}
                  — {accessDate(a)}
                </span>
              )}
              {a.is_scanned && (
                <span className="text-muted-foreground"> (utilisé)</span>
              )}
            </li>
          ))}
        </ul>
        {reservationRef && (
          <p className="mt-1 border-t border-dashed pt-1 font-mono text-[8px] tracking-wide text-muted-foreground">
            Réf. {shortRef(reservationRef)}
          </p>
        )}
      </div>
    </div>
  );
}
