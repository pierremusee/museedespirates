import { PosTerminal } from "./pos-terminal";

type ProductRead = {
  code: string;
  label: string;
  kind: "simple" | "pass" | "family" | "group";
  price_adult: string | null;
  price_child: string | null;
  price_reduced: string | null;
  family_base_price: string | null;
  extra_child_price: string | null;
  is_addon: boolean;
  components: { component_type: string; quantity: number }[];
};

type SessionRead = {
  id: string;
  start_time: string;
  remaining_capacity: number;
  is_expired: boolean;
};

type EventRead = {
  id: string;
  title: string;
  event_type: string;
  sessions: SessionRead[];
};

const timeFormatter = new Intl.DateTimeFormat("fr-FR", {
  timeStyle: "short",
  timeZone: "Europe/Paris",
});

function todayParis(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Paris" }).format(
    new Date()
  );
}

export function extractTheaterSessions(events: EventRead[]) {
  return events
    .filter((e) => e.event_type === "theater")
    .flatMap((e) =>
      e.sessions.map((s) => ({
        id: s.id,
        startTime: s.start_time,
        label: timeFormatter.format(new Date(s.start_time)),
        show: e.title,
        remaining: s.remaining_capacity,
        expired: s.is_expired,
      }))
    )
    .sort((a, b) => a.startTime.localeCompare(b.startTime));
}

export default async function CaissePage() {
  const api = process.env.NEXT_PUBLIC_API_URL;
  const today = todayParis();

  const [productsRes, eventsRes, seasonRes] = await Promise.all([
    fetch(`${api}/products`, { cache: "no-store" }),
    fetch(`${api}/events?date=${today}`, { cache: "no-store" }),
    fetch(`${api}/seasonal/check?date=${today}`, { cache: "no-store" }),
  ]);
  if (!productsRes.ok || !eventsRes.ok || !seasonRes.ok) {
    throw new Error("API indisponible");
  }

  const products: ProductRead[] = await productsRes.json();
  const sessions = extractTheaterSessions(await eventsRes.json());
  const season: { high_season: boolean } = await seasonRes.json();

  return (
    <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-8">
      <PosTerminal
        products={products}
        initialSessions={sessions}
        initialDate={today}
        initialHighSeason={season.high_season}
      />
    </main>
  );
}
