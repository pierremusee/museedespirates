import { Anchor, CalendarClock, Flame, Users } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { DatePicker } from "./date-picker";
import { ReservationDialog } from "./reservation-dialog";

type SessionRead = {
  id: string;
  start_time: string;
  max_capacity: number;
  booked_seats: number;
  remaining_capacity: number;
};

type EventRead = {
  id: string;
  title: string;
  description: string | null;
  event_type: "permanent_exhibition" | "theater" | "guided_tour";
  is_active: boolean;
  sessions: SessionRead[];
};

type ProductComponentRead = {
  component_type: "museum_day" | "theater_session" | "dining_session";
  quantity: number;
  event_id: string | null;
};

type ProductRead = {
  id: string;
  code: string;
  label: string;
  kind: "simple" | "pass" | "family" | "group";
  price_adult: string | null;
  price_child: string | null;
  price_reduced: string | null;
  family_base_price: string | null;
  extra_child_price: string | null;
  is_addon: boolean;
  components: ProductComponentRead[];
};

const KIND_LABELS: Record<ProductRead["kind"], string> = {
  simple: "Billet simple",
  pass: "Pass combiné",
  family: "Forfait famille",
  group: "Offre groupes (8+)",
};

const COMPONENT_LABELS: Record<
  ProductComponentRead["component_type"],
  string
> = {
  museum_day: "Accès Musée",
  theater_session: "Séance Théâtre",
  dining_session: "Dîner-spectacle",
};

const timeFormatter = new Intl.DateTimeFormat("fr-FR", {
  timeStyle: "short",
  timeZone: "Europe/Paris",
});

const eurFormatter = new Intl.NumberFormat("fr-FR", {
  style: "currency",
  currency: "EUR",
});

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function todayParis(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Paris" }).format(
    new Date()
  );
}

function describeComponents(product: ProductRead): string {
  return product.components
    .map((c) =>
      c.quantity > 1
        ? `${COMPONENT_LABELS[c.component_type]} x${c.quantity}`
        : COMPONENT_LABELS[c.component_type]
    )
    .join(" + ");
}

function priceSummary(product: ProductRead): string {
  if (product.kind === "family") {
    return `${eurFormatter.format(Number(product.family_base_price))} (2A + 2E)`;
  }
  if (product.kind === "group") {
    return `${eurFormatter.format(Number(product.price_adult))} / personne`;
  }
  return `${eurFormatter.format(Number(product.price_adult))} adulte`;
}

export default async function ReserverPage({
  searchParams,
}: {
  searchParams: Promise<{ date?: string }>;
}) {
  const { date } = await searchParams;
  const selectedDate = date && DATE_RE.test(date) ? date : todayParis();
  const api = process.env.NEXT_PUBLIC_API_URL;

  const [eventsRes, productsRes, seasonRes] = await Promise.all([
    fetch(`${api}/events?date=${selectedDate}`, { cache: "no-store" }),
    fetch(`${api}/products`, { cache: "no-store" }),
    fetch(`${api}/seasonal/check?date=${selectedDate}`, {
      cache: "no-store",
    }),
  ]);
  if (!eventsRes.ok || !productsRes.ok || !seasonRes.ok) {
    throw new Error("API indisponible");
  }
  const events: EventRead[] = await eventsRes.json();
  const products: ProductRead[] = await productsRes.json();
  const season: { high_season: boolean } = await seasonRes.json();

  // Programme du Théâtre du Kraken : chaque pièce est un événement
  // `theater` distinct, avec sa description et ses séances du jour.
  const theaterShows = events
    .filter((e) => e.event_type === "theater" && e.sessions.length > 0)
    .sort((a, b) =>
      a.sessions[0].start_time.localeCompare(b.sessions[0].start_time)
    );
  const theaterSessions = theaterShows
    .flatMap((e) =>
      e.sessions.map((s) => ({
        id: s.id,
        startTime: s.start_time,
        label: timeFormatter.format(new Date(s.start_time)),
        show: e.title,
        remaining: s.remaining_capacity,
      }))
    )
    .sort((a, b) => a.startTime.localeCompare(b.startTime));

  // Séance additionnelle à tarif réduit : proposée en option dans le
  // dialogue de réservation (fusionnée sur le même QR, même réservation).
  const extraShow = products.find((p) => p.code === "extra_show") ?? null;

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-10">
      <h1 className="mb-2 text-3xl font-bold tracking-tight">
        Réserver vos billets
      </h1>
      <p className="mb-6 text-muted-foreground">
        Choisissez votre jour de visite, puis un billet ou un pass parmi les
        offres du Musée des Pirates.
      </p>

      <div className="mb-8 flex flex-wrap items-center gap-3">
        <DatePicker value={selectedDate} />
        {season.high_season && (
          <span className="inline-flex items-center gap-1.5 rounded-full bg-secondary px-3 py-1 text-xs font-medium text-secondary-foreground">
            <Flame className="size-3.5 text-orange-500" />
            Haute saison — tarifs majorés
          </span>
        )}
      </div>

      {theaterShows.length > 0 && (
        <div className="mb-8 space-y-3">
          <p className="flex items-center gap-2 text-sm font-medium">
            <CalendarClock className="size-4" />
            Au Théâtre du Kraken aujourd&apos;hui :
          </p>
          <div className="grid gap-3 md:grid-cols-2">
            {theaterShows.map((show) => (
              <div
                key={show.id}
                className="rounded-lg border bg-card px-4 py-3"
              >
                <p className="text-sm font-semibold">{show.title}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {show.sessions
                    .map(
                      (s) =>
                        `${timeFormatter.format(
                          new Date(s.start_time)
                        )} (${s.remaining_capacity} places)`
                    )
                    .join(" · ")}
                </p>
                {show.description && (
                  <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                    {show.description}
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {products.length === 0 ? (
        <Card className="py-10 text-center">
          <CardContent className="flex flex-col items-center gap-3">
            <Anchor className="size-10 text-muted-foreground" />
            <p className="text-lg font-medium">Aucune offre disponible</p>
            <p className="text-sm text-muted-foreground">
              Nos pirates préparent la billetterie. Revenez bientôt.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
          {products
            .filter((p) => !p.is_addon)
            .map((product) => {
            const sessionCount = product.components
              .filter((c) => c.component_type !== "museum_day")
              .reduce((sum, c) => sum + c.quantity, 0);
            const hasMuseumDay = product.components.some(
              (c) => c.component_type === "museum_day"
            );
            const minRemaining =
              sessionCount > 0 && theaterSessions.length > 0
                ? Math.min(...theaterSessions.map((s) => s.remaining))
                : null;
            const soldOut =
              sessionCount > 0 &&
              (theaterSessions.length === 0 || minRemaining === 0);

            return (
              <Card key={product.id} className="flex flex-col">
                <CardHeader>
                  <CardDescription>{KIND_LABELS[product.kind]}</CardDescription>
                  <CardTitle className="text-lg">{product.label}</CardTitle>
                </CardHeader>
                <CardContent className="flex-1 space-y-3">
                  <p className="text-sm text-muted-foreground">
                    {describeComponents(product)}
                  </p>
                  <p className="text-lg font-semibold">
                    {priceSummary(product)}
                  </p>
                  {product.kind === "family" && (
                    <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      <Users className="size-3.5" />+
                      {eurFormatter.format(
                        Number(product.extra_child_price)
                      )}{" "}
                      par enfant supplémentaire
                    </p>
                  )}
                  {product.kind === "group" && (
                    <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      <Users className="size-3.5" />
                      À partir de 8 personnes — réservation au guichet
                      ou sur devis.
                    </p>
                  )}
                  {sessionCount > 0 && theaterSessions.length === 0 && (
                    <p className="text-xs text-amber-600">
                      Aucune séance ce jour — choisissez une autre date.
                    </p>
                  )}
                </CardContent>
                <CardFooter>
                  {product.kind === "group" ? (
                    <span className="inline-flex h-8 items-center rounded-md border px-3 text-xs text-muted-foreground">
                      Guichet uniquement
                    </span>
                  ) : (
                    <ReservationDialog
                      product={product}
                      // La séance supplémentaire (add-on) n'est proposée
                      // que sur les produits accordant déjà une séance.
                      extraProduct={
                        sessionCount > 0 ? extraShow : null
                      }
                      sessions={theaterSessions}
                      visitDate={selectedDate}
                      highSeason={season.high_season}
                      sessionCount={sessionCount}
                      hasMuseumDay={hasMuseumDay}
                      soldOut={soldOut}
                    />
                  )}
                </CardFooter>
              </Card>
            );
          })}
        </div>
      )}
    </main>
  );
}
