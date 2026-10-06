import Link from "next/link";
import { CircleCheck, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { TicketQR } from "./ticket-qr";

type AccessRead = {
  id: string;
  access_type: "open_ticket" | "session_standard" | "session_dining";
  session_id: string | null;
  event_id: string | null;
  valid_date: string | null;
  is_scanned: boolean;
  session_start: string | null;
};

type TicketRead = {
  id: string;
  ticket_category: "adult" | "child" | "reduced" | "group" | "school";
  accesses: AccessRead[];
};

type ItemRead = {
  id: string;
  product_code: string;
  category: string | null;
  extra_children: number;
  free_profile: string | null;
  computed_price: string;
  season_modifier: string;
};

type ReservationRead = {
  id: string;
  customer_email: string;
  total_price: string;
  status: string;
  created_at: string;
  items: ItemRead[];
  tickets: TicketRead[];
};

const CATEGORY_LABELS: Record<TicketRead["ticket_category"], string> = {
  adult: "Adulte",
  child: "Enfant",
  reduced: "Tarif réduit",
  group: "Groupe",
  school: "Scolaire",
};

const TYPE_LABELS: Record<AccessRead["access_type"], string> = {
  open_ticket: "Musée",
  session_standard: "Théâtre",
  session_dining: "Dîner-spectacle",
};

const dateTimeFormatter = new Intl.DateTimeFormat("fr-FR", {
  dateStyle: "short",
  timeStyle: "short",
  timeZone: "Europe/Paris",
});

const FREE_PROFILE_LABELS: Record<string, string> = {
  under_4: "moins de 4 ans",
  disability: "carte d'invalidité",
  pmr_companion: "accompagnateur PMR",
};

const eurFormatter = new Intl.NumberFormat("fr-FR", {
  style: "currency",
  currency: "EUR",
});

async function getReservation(id: string): Promise<ReservationRead | null> {
  const res = await fetch(
    `${process.env.NEXT_PUBLIC_API_URL}/reservations/${id}`,
    { cache: "no-store" }
  );
  if (!res.ok) return null;
  return res.json();
}

export default async function SuccesPage({
  searchParams,
}: {
  searchParams?: Promise<{ id?: string }>;
}) {
  const params = await searchParams;
  const reservation = params?.id ? await getReservation(params.id) : null;

  if (!reservation) {
    return (
      <main className="flex flex-1 items-center justify-center px-4">
        <Card className="w-full max-w-md text-center">
          <CardHeader className="items-center">
            <TriangleAlert className="mb-2 size-12 text-amber-500" aria-hidden />
            <CardTitle className="text-2xl">Réservation introuvable</CardTitle>
            <CardDescription>
              Impossible de retrouver cette réservation. Vérifiez le lien ou
              réessayez.
            </CardDescription>
          </CardHeader>
          <CardFooter className="justify-center">
            <Button asChild variant="outline">
              <Link href="/reserver">Retour à la billetterie</Link>
            </Button>
          </CardFooter>
        </Card>
      </main>
    );
  }

  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-10">
      <Card>
        <CardHeader className="items-center text-center">
          <CircleCheck className="mb-2 size-12 text-green-600" aria-hidden />
          <CardTitle className="text-2xl">Réservation confirmée</CardTitle>
          <CardDescription>
            {reservation.customer_email} — {reservation.tickets.length} billet
            {reservation.tickets.length > 1 ? "s" : ""}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="mb-6 space-y-2">
            {reservation.items.map((item) => (
              <li
                key={item.id}
                className="flex items-center justify-between rounded-lg border px-4 py-2 text-sm"
              >
                <span>
                  <span className="font-medium">{item.product_code}</span>
                  {item.category && (
                    <span className="text-muted-foreground">
                      {" "}
                      — {item.category}
                    </span>
                  )}
                  {item.free_profile && (
                    <span className="text-muted-foreground">
                      {" "}
                      — gratuit ({FREE_PROFILE_LABELS[item.free_profile]})
                    </span>
                  )}
                  {item.extra_children > 0 && (
                    <span className="text-muted-foreground">
                      {" "}
                      +{item.extra_children} enfant
                      {item.extra_children > 1 ? "s" : ""} suppl.
                    </span>
                  )}
                </span>
                <span className="tabular-nums">
                  {eurFormatter.format(Number(item.computed_price))}
                </span>
              </li>
            ))}
          </ul>
          <div className="mb-6 flex items-center justify-between rounded-lg border bg-muted/40 px-4 py-3">
            <span className="text-sm font-medium">Total de la commande</span>
            <span className="text-lg font-bold tabular-nums">
              {eurFormatter.format(Number(reservation.total_price))}
            </span>
          </div>
          <p className="mb-6 text-center text-xs text-muted-foreground">
            Référence : <span className="font-mono">{reservation.id}</span>
          </p>
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {reservation.tickets.map((ticket) => (
              <div
                key={ticket.id}
                className="flex flex-col items-center gap-3 rounded-lg border p-4"
              >
                <div className="rounded-md bg-white p-2">
                  <TicketQR value={ticket.id} />
                </div>
                <div className="text-center">
                  <p className="text-sm font-medium">
                    {CATEGORY_LABELS[ticket.ticket_category]}
                  </p>
                  <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">
                    {ticket.accesses.map((a) => (
                      <li key={a.id}>
                        {TYPE_LABELS[a.access_type]}
                        {a.session_start
                          ? ` — ${dateTimeFormatter.format(
                              new Date(a.session_start)
                            )}`
                          : a.valid_date
                            ? ` — ${new Date(
                                `${a.valid_date}T12:00:00`
                              ).toLocaleDateString("fr-FR")}`
                            : ""}
                        {a.is_scanned && " (utilisé)"}
                      </li>
                    ))}
                  </ul>
                  <p className="font-mono text-xs text-muted-foreground">
                    {ticket.id.slice(0, 8)}…
                  </p>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
        <CardFooter className="justify-center">
          <Button asChild variant="outline">
            <Link href="/reserver">Retour à la billetterie</Link>
          </Button>
        </CardFooter>
      </Card>
    </main>
  );
}
