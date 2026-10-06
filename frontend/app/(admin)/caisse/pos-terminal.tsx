"use client";

import { useState } from "react";
import {
  Banknote,
  ChevronDown,
  CreditCard,
  FileText,
  Loader2,
  Minus,
  Pencil,
  Plus,
  Printer,
  RotateCcw,
  Ticket,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { TicketCard } from "@/components/ticket-card";
import { PrintTicketsButton } from "@/components/print-tickets-button";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type SessionOption = {
  id: string;
  label: string;
  show: string;
  remaining: number;
  expired: boolean;
};

type Product = {
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

type Tariff =
  | "adult"
  | "child"
  | "reduced"
  | "under_4"
  | "disability"
  | "pmr_companion";

type PaymentMethod = "cb" | "cash" | "ancv" | "check";

// Une ligne = un produit × N personnes. Pour `simple`/`pass`, `counts`
// porte le nombre de personnes par tarif (y compris profils gratuits) ;
// `family` et `group` ont leur composition propre (2A+2E / groupSize).
type CartLine = {
  id: number;
  product: Product;
  counts: Record<Tariff, number>;
  groupSize: number;
  extraChildren: number;
  sessionIds: string[];
};

type OrderState = {
  id: string;
  total: number;
  paid: number;
  due: number;
  status: "pending" | "confirmed" | "cancelled";
};

type PaymentEntry = {
  method: PaymentMethod;
  amount: number;
  applied: number;
  change: number;
};

type AccessRead = {
  id: string;
  access_type: string;
  session_start: string | null;
  session_event_title: string | null;
  valid_date: string | null;
  is_scanned: boolean;
};

type TicketRead = {
  id: string;
  ticket_category: string;
  accesses: AccessRead[];
};

// ---------------------------------------------------------------------------
// Constantes métier
// ---------------------------------------------------------------------------

const API = process.env.NEXT_PUBLIC_API_URL;
const MIN_GROUP_SIZE = 8;

const MOD_ADULT_REDUCED = 2;
const MOD_CHILD = 1;
const MOD_FAMILY = 5;

const FREE_TARIFFS = new Set<Tariff>([
  "under_4",
  "disability",
  "pmr_companion",
]);

const PAYMENT_METHODS: {
  key: PaymentMethod;
  label: string;
  icon: typeof CreditCard;
}[] = [
  { key: "cb", label: "CB", icon: CreditCard },
  { key: "cash", label: "Espèces", icon: Banknote },
  { key: "ancv", label: "ANCV", icon: Ticket },
  { key: "check", label: "Chèque", icon: FileText },
];

const PAID_TARIFFS: Tariff[] = ["adult", "child", "reduced"];
const FREE_PROFILE_TARIFFS: Tariff[] = [
  "under_4",
  "disability",
  "pmr_companion",
];
const ALL_TARIFFS: Tariff[] = [...PAID_TARIFFS, ...FREE_PROFILE_TARIFFS];

const TARIFF_SHORT: Record<Tariff, string> = {
  adult: "Adulte",
  child: "Enfant",
  reduced: "Réduit",
  under_4: "-4 ans",
  disability: "Invalidité",
  pmr_companion: "PMR",
};

const eurFormatter = new Intl.NumberFormat("fr-FR", {
  style: "currency",
  currency: "EUR",
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function sessionCountOf(product: Product): number {
  return product.components
    .filter((c) => c.component_type !== "museum_day")
    .reduce((sum, c) => sum + c.quantity, 0);
}

function hasMuseumDay(product: Product): boolean {
  return product.components.some((c) => c.component_type === "museum_day");
}

function zeroCounts(): Record<Tariff, number> {
  return {
    adult: 0,
    child: 0,
    reduced: 0,
    under_4: 0,
    disability: 0,
    pmr_companion: 0,
  };
}

// Composition d'une ligne en personnes par tarif — family/group ont une
// composition fixe dérivée de leurs propres champs.
function countsOf(line: CartLine): Record<Tariff, number> {
  if (line.product.kind === "family") {
    return { ...zeroCounts(), adult: 2, child: 2 + line.extraChildren };
  }
  if (line.product.kind === "group") {
    return { ...zeroCounts(), adult: line.groupSize };
  }
  return line.counts;
}

function linePersons(line: CartLine): number {
  const c = countsOf(line);
  return ALL_TARIFFS.reduce((sum, t) => sum + c[t], 0);
}

function lineEstimate(line: CartLine, highSeason: boolean): number {
  const p = line.product;
  if (p.kind === "family") {
    return (
      Number(p.family_base_price) +
      line.extraChildren * Number(p.extra_child_price) +
      (highSeason ? MOD_FAMILY : 0)
    );
  }
  if (p.kind === "group") {
    return (
      line.groupSize *
      (Number(p.price_adult) + (highSeason ? MOD_ADULT_REDUCED : 0))
    );
  }
  const c = line.counts;
  return (
    c.adult * (Number(p.price_adult) + (highSeason ? MOD_ADULT_REDUCED : 0)) +
    c.reduced *
      (Number(p.price_reduced) + (highSeason ? MOD_ADULT_REDUCED : 0)) +
    c.child * (Number(p.price_child) + (highSeason ? MOD_CHILD : 0))
  );
}

function autoSessions(
  product: Product,
  sessions: SessionOption[]
): string[] {
  const need = sessionCountOf(product);
  if (need === 0) return [];
  const available = sessions
    .filter((s) => s.remaining > 0 && !s.expired)
    .map((s) => s.id);
  const picks = available.slice(0, need);
  while (picks.length < need) picks.push("");
  return picks;
}

function autoSessionExcluding(
  sessions: SessionOption[],
  exclude: string[]
): string {
  return (
    sessions.find(
      (s) => s.remaining > 0 && !s.expired && !exclude.includes(s.id)
    )?.id ?? ""
  );
}

const PASS_1_CODE = "pass_1_show";

// ---------------------------------------------------------------------------
// Composant principal
// ---------------------------------------------------------------------------

export function PosTerminal({
  products,
  initialSessions,
  initialDate,
  initialHighSeason,
}: {
  products: Product[];
  initialSessions: SessionOption[];
  initialDate: string;
  initialHighSeason: boolean;
}) {
  const [visitDate, setVisitDate] = useState(initialDate);
  const [sessions, setSessions] = useState(initialSessions);
  const [highSeason, setHighSeason] = useState(initialHighSeason);

  const [lines, setLines] = useState<CartLine[]>([]);
  const [nextId, setNextId] = useState(1);
  const [email, setEmail] = useState("");

  const [order, setOrder] = useState<OrderState | null>(null);
  const [extraOpen, setExtraOpen] = useState(false);
  const [payments, setPayments] = useState<PaymentEntry[]>([]);
  const [tickets, setTickets] = useState<TicketRead[]>([]);
  const [showTickets, setShowTickets] = useState(false);

  const [method, setMethod] = useState<PaymentMethod>("cash");
  const [amountStr, setAmountStr] = useState("");
  const [busy, setBusy] = useState(false);

  const hasGroup = lines.some((l) => l.product.kind === "group");
  const estimate = lines.reduce(
    (sum, l) => sum + lineEstimate(l, highSeason),
    0
  );
  const regularLines = lines.filter((l) => !l.product.is_addon);
  const extraLines = lines.filter((l) => l.product.is_addon);
  const extraTotal = extraLines.reduce(
    (sum, l) => sum + lineEstimate(l, highSeason),
    0
  );
  const extraErrCount = extraLines.filter(
    (l) => lineErrors(l) !== null
  ).length;

  // -------------------------------------------------------------------------
  // Panier
  // -------------------------------------------------------------------------

  function addLine(product: Product) {
    if (product.kind === "simple" || product.kind === "pass") {
      // Produit déjà au panier : +1 adulte sur la ligne existante.
      const same = regularLines.find((l) => l.product.code === product.code);
      if (same) {
        updateLine(same.id, {
          counts: { ...same.counts, adult: same.counts.adult + 1 },
        });
        return;
      }
      // Panier déjà rempli : la nouvelle ligne reprend la composition des
      // personnes présentes (ex. 3 entrées musée puis clic « Théâtre » →
      // 2 adultes + 1 enfant d'emblée).
      const counts = zeroCounts();
      if (regularLines.length > 0) {
        for (const l of regularLines) {
          const c = countsOf(l);
          for (const t of ALL_TARIFFS) counts[t] += c[t];
        }
      } else {
        counts.adult = 1;
      }
      setLines((prev) => [
        ...prev,
        {
          id: nextId,
          product,
          counts,
          groupSize: MIN_GROUP_SIZE,
          extraChildren: 0,
          sessionIds: autoSessions(product, sessions),
        },
      ]);
      setNextId((n) => n + 1);
      return;
    }
    setLines((prev) => [
      ...prev,
      {
        id: nextId,
        product,
        counts: zeroCounts(),
        groupSize: MIN_GROUP_SIZE,
        extraChildren: 0,
        sessionIds: autoSessions(product, sessions),
      },
    ]);
    setNextId((n) => n + 1);
  }

  function addExtraShow(product: Product) {
    // Add-on (règle serveur is_addon) : une ligne par ligne du panier
    // accordant déjà une séance — mêmes personnes, sur une séance
    // différente de celle(s) déjà choisies.
    const sessionLines = regularLines.filter(
      (l) => sessionCountOf(l.product) > 0
    );
    if (sessionLines.length === 0) {
      toast.error(
        "La séance supplémentaire exige un billet ou un pass avec séance dans le panier."
      );
      return;
    }
    setLines((prev) => [
      ...prev,
      ...sessionLines.map((src, i) => ({
        id: nextId + i,
        product,
        counts: { ...countsOf(src) },
        groupSize: MIN_GROUP_SIZE,
        extraChildren: 0,
        sessionIds: [
          autoSessionExcluding(sessions, src.sessionIds.filter(Boolean)),
        ],
      })),
    ]);
    const n = sessionLines.reduce((sum, l) => sum + linePersons(l), 0);
    setNextId((id) => id + sessionLines.length);
    toast.success(
      `Séance supplémentaire ajoutée pour les ${n} personne(s) disposant d'une séance — ajustez les lignes non souhaitées.`
    );
  }

  function bumpCount(id: number, tariff: Tariff, delta: number) {
    setLines((prev) =>
      prev.map((l) =>
        l.id === id
          ? {
              ...l,
              counts: {
                ...l.counts,
                [tariff]: Math.max(0, l.counts[tariff] + delta),
              },
            }
          : l
      )
    );
  }

  // -------------------------------------------------------------------------
  // Suggestion « Pass 1 Spectacle » : musée + séance vendus à l'unité
  // -------------------------------------------------------------------------

  const isMuseumOnly = (l: CartLine) =>
    l.product.kind === "simple" &&
    hasMuseumDay(l.product) &&
    sessionCountOf(l.product) === 0;
  const isShowOnly = (l: CartLine) =>
    l.product.kind === "simple" &&
    !l.product.is_addon &&
    !hasMuseumDay(l.product) &&
    sessionCountOf(l.product) > 0;

  function passSuggestion(): {
    counts: Record<Tariff, number>;
    persons: number;
    savings: number;
    sessionId: string;
  } | null {
    const pass = products.find((p) => p.code === PASS_1_CODE);
    const museumLines = regularLines.filter(isMuseumOnly);
    const showLines = regularLines.filter(isShowOnly);
    if (!pass || museumLines.length === 0 || showLines.length === 0) {
      return null;
    }
    const counts = zeroCounts();
    for (const t of ALL_TARIFFS) {
      counts[t] = Math.min(
        museumLines.reduce((s, l) => s + l.counts[t], 0),
        showLines.reduce((s, l) => s + l.counts[t], 0)
      );
    }
    const persons = ALL_TARIFFS.reduce((s, t) => s + counts[t], 0);
    if (persons === 0) return null;
    const unit = (p: Product, t: Tariff) =>
      t === "adult"
        ? Number(p.price_adult)
        : t === "child"
          ? Number(p.price_child)
          : t === "reduced"
            ? Number(p.price_reduced)
            : 0;
    const museum = museumLines[0].product;
    const show = showLines[0].product;
    // Le modificateur haute saison s'annule des deux côtés : l'économie
    // ne dépend que des prix unitaires du catalogue.
    const savings = PAID_TARIFFS.reduce(
      (s, t) => s + counts[t] * (unit(museum, t) + unit(show, t) - unit(pass, t)),
      0
    );
    if (savings <= 0) return null;
    const sessionId =
      showLines.flatMap((l) => l.sessionIds).find(Boolean) ?? "";
    return { counts, persons, savings, sessionId };
  }

  function convertToPass() {
    const hint = passSuggestion();
    const pass = products.find((p) => p.code === PASS_1_CODE);
    if (!hint || !pass) return;
    const toMove = { ...hint.counts };
    const consume = (pred: (l: CartLine) => boolean) => (l: CartLine) => {
      if (!pred(l)) return l;
      const c = { ...l.counts };
      for (const t of ALL_TARIFFS) {
        const take = Math.min(c[t], toMove[t]);
        c[t] -= take;
        toMove[t] -= take;
      }
      return { ...l, counts: c };
    };
    const next = lines
      .map(consume(isMuseumOnly))
      .map(consume(isShowOnly))
      .filter((l) => linePersons(l) > 0);
    next.push({
      id: nextId,
      product: pass,
      counts: { ...hint.counts },
      groupSize: MIN_GROUP_SIZE,
      extraChildren: 0,
      sessionIds: [hint.sessionId],
    });
    setLines(next);
    setNextId((n) => n + 1);
    toast.success(
      `Pass 1 Spectacle ×${hint.persons} — économie ${eurFormatter.format(hint.savings)}`
    );
  }

  function updateLine(id: number, patch: Partial<CartLine>) {
    setLines((prev) =>
      prev.map((l) => (l.id === id ? { ...l, ...patch } : l))
    );
  }

  function removeLine(id: number) {
    setLines((prev) => prev.filter((l) => l.id !== id));
  }

  async function changeDate(value: string) {
    setVisitDate(value);
    try {
      const [evRes, seRes] = await Promise.all([
        fetch(`${API}/events?date=${value}`, { cache: "no-store" }),
        fetch(`${API}/seasonal/check?date=${value}`, { cache: "no-store" }),
      ]);
      if (!evRes.ok || !seRes.ok) throw new Error();
      const events: {
        title: string;
        event_type: string;
        sessions: {
          id: string;
          start_time: string;
          remaining_capacity: number;
          is_expired: boolean;
        }[];
      }[] = await evRes.json();
      const timeFmt = new Intl.DateTimeFormat("fr-FR", {
        timeStyle: "short",
        timeZone: "Europe/Paris",
      });
      const newSessions = events
        .filter((e) => e.event_type === "theater")
        .flatMap((e) =>
          e.sessions.map((s) => ({
            id: s.id,
            startTime: s.start_time,
            label: timeFmt.format(new Date(s.start_time)),
            show: e.title,
            remaining: s.remaining_capacity,
            expired: s.is_expired,
          }))
        )
        .sort((a, b) => a.startTime.localeCompare(b.startTime));
      setSessions(newSessions);
      setHighSeason((await seRes.json()).high_season);
      // Les séances choisies ne sont plus valables : ré-auto-sélection.
      setLines((prev) =>
        prev.map((l) => ({
          ...l,
          sessionIds: autoSessions(l.product, newSessions),
        }))
      );
    } catch {
      toast.error("Impossible de charger les séances de cette date.");
    }
  }

  function lineErrors(line: CartLine): string | null {
    const need = sessionCountOf(line.product);
    if (need > 0) {
      const picked = line.sessionIds.filter(Boolean);
      if (picked.length !== need || new Set(picked).size !== need) {
        return `${need} séance(s) distincte(s) requise(s)`;
      }
      const cap = Math.min(
        ...picked.map(
          (id) => sessions.find((s) => s.id === id)?.remaining ?? 0
        )
      );
      if (linePersons(line) > cap) {
        return `places insuffisantes (${cap} restantes)`;
      }
    }
    const c = countsOf(line);
    if (c.pmr_companion > c.disability) {
      return "1 accompagnateur PMR par personne en invalidité";
    }
    if (
      line.product.kind === "group" &&
      line.groupSize < MIN_GROUP_SIZE
    ) {
      return `groupe : ${MIN_GROUP_SIZE} pers. minimum`;
    }
    if (linePersons(line) === 0) {
      return "aucune personne sur la ligne";
    }
    if (line.product.is_addon) {
      // Règle serveur is_addon : les personnes en séance supplémentaire
      // ne peuvent excéder celles couvertes par un billet/pass à séance.
      const basePersons = regularLines
        .filter((l) => sessionCountOf(l.product) > 0)
        .reduce((s, l) => s + linePersons(l), 0);
      const extraPersons = extraLines.reduce(
        (s, l) => s + linePersons(l),
        0
      );
      if (extraPersons > basePersons) {
        return "exige un billet/pass avec séance pour chaque personne";
      }
    }
    return null;
  }

  function buildItems() {
    // Expansion : une ligne à compteurs produit 1 item par personne,
    // mêmes conventions que la billetterie web (free_profile sans
    // catégorie sauf disability qui exige category=adult).
    return lines.flatMap((l) => {
      const base = {
        product_code: l.product.code,
        ...(hasMuseumDay(l.product) ? { visit_date: visitDate } : {}),
        ...(sessionCountOf(l.product) > 0
          ? { session_ids: l.sessionIds.filter(Boolean) }
          : {}),
      };
      if (l.product.kind === "family") {
        return [{ ...base, extra_children: l.extraChildren }];
      }
      if (l.product.kind === "group") {
        return [{ ...base, group_size: l.groupSize }];
      }
      const items: Record<string, unknown>[] = [];
      for (const t of PAID_TARIFFS) {
        for (let i = 0; i < l.counts[t]; i++) {
          items.push({ ...base, category: t });
        }
      }
      for (const t of FREE_PROFILE_TARIFFS) {
        for (let i = 0; i < l.counts[t]; i++) {
          items.push({
            ...base,
            free_profile: t,
            ...(t === "disability" ? { category: "adult" } : {}),
          });
        }
      }
      return items;
    });
  }

  async function submitOrder() {
    const bad = lines.find((l) => lineErrors(l) !== null);
    if (bad) {
      toast.error(`${bad.product.label} : ${lineErrors(bad)}`);
      return;
    }
    setBusy(true);
    try {
      const res = await fetch(`${API}/reservations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          customer_email: email || "guichet@musee-pirates.fr",
          channel: "pos",
          items: buildItems(),
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        toast.error(body.detail ?? `Erreur ${res.status} à la commande.`);
        return;
      }
      setOrder({
        id: body.id,
        total: Number(body.total_price),
        paid: Number(body.paid_amount),
        due: Number(body.amount_due),
        status: body.status,
      });
      if (body.status === "confirmed") {
        setTickets(body.tickets ?? []);
        toast.success("Commande à 0 € — billets prêts à émettre.");
      } else {
        setAmountStr(body.amount_due);
        toast.success("Commande créée — place à l'encaissement.");
      }
    } catch {
      toast.error("Serveur inaccessible.");
    } finally {
      setBusy(false);
    }
  }

  // -------------------------------------------------------------------------
  // Encaissement
  // -------------------------------------------------------------------------

  const amount = Number(amountStr) || 0;
  const previewChange =
    order && method === "cash" && amount > order.due
      ? amount - order.due
      : 0;
  const ancvOverflow =
    order && method === "ancv" && amount > order.due
      ? amount - order.due
      : 0;

  async function pay() {
    if (!order || amount <= 0 || busy) return;
    setBusy(true);
    try {
      const res = await fetch(`${API}/reservations/${order.id}/payments`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ method, amount: amount.toFixed(2) }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        toast.error(body.detail ?? `Erreur ${res.status} au paiement.`);
        return;
      }
      const change = Number(body.change_due);
      setPayments((prev) => [
        ...prev,
        {
          method,
          amount: Number(body.payment.amount),
          applied: Number(body.payment.applied_amount),
          change,
        },
      ]);
      setOrder((o) =>
        o
          ? {
              ...o,
              paid: o.total - Number(body.amount_due),
              due: Number(body.amount_due),
              status: body.reservation_status,
            }
          : o
      );
      if (body.reservation_status === "confirmed") {
        setTickets(body.tickets ?? []);
        toast.success("Solde atteint — commande confirmée !");
      } else {
        toast.success(
          `Encaissé ${eurFormatter.format(Number(body.payment.applied_amount))} — reste ${eurFormatter.format(Number(body.amount_due))}`
        );
      }
      if (change > 0) {
        toast.info(`Rendre ${eurFormatter.format(change)} au client.`, {
          duration: 10000,
        });
      }
      setAmountStr(body.amount_due > 0 ? String(body.amount_due) : "");
    } catch {
      toast.error("Serveur inaccessible.");
    } finally {
      setBusy(false);
    }
  }

  async function cancelOrder() {
    if (!order || order.status !== "pending" || busy) return;
    setBusy(true);
    try {
      const res = await fetch(
        `${API}/reservations/${order.id}/cancel`,
        { method: "POST" }
      );
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        toast.error(body.detail ?? `Erreur ${res.status} à l'annulation.`);
        return;
      }
      const refunded = order.paid;
      setOrder(null);
      setPayments([]);
      setAmountStr("");
      toast.success(
        refunded > 0
          ? `Commande annulée — rembourser ${eurFormatter.format(refunded)} déjà encaissés, puis ajustez le panier.`
          : "Commande annulée — panier déverrouillé, ajustez puis revalidez.",
        { duration: 8000 }
      );
    } catch {
      toast.error("Serveur inaccessible.");
    } finally {
      setBusy(false);
    }
  }

  function reset() {
    setLines([]);
    setOrder(null);
    setPayments([]);
    setTickets([]);
    setShowTickets(false);
    setAmountStr("");
    setMethod("cash");
    setEmail("");
  }

  // -------------------------------------------------------------------------
  // Rendu
  // -------------------------------------------------------------------------

  const locked = order !== null;
  const passHint = locked ? null : passSuggestion();

  function renderLine(l: CartLine) {
    const err = lineErrors(l);
    const need = sessionCountOf(l.product);
    return (
      <div
        key={l.id}
        className="space-y-2 rounded-lg border px-3 py-2"
      >
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm font-medium">
            {l.product.label}
            <span className="ml-1.5 text-xs font-normal text-muted-foreground">
              ×{linePersons(l)} pers.
            </span>
          </span>
          <div className="flex items-center gap-2">
            <span className="text-sm tabular-nums">
              {eurFormatter.format(lineEstimate(l, highSeason))}
            </span>
            {!locked && (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="size-6"
                onClick={() => removeLine(l.id)}
              >
                <Trash2 className="size-3.5" />
              </Button>
            )}
          </div>
        </div>

        {l.product.kind === "group" && (
          <div className="flex items-center gap-2 text-xs">
            <Label className="text-xs">Personnes</Label>
            <Input
              type="number"
              min={MIN_GROUP_SIZE}
              value={l.groupSize}
              disabled={locked}
              onChange={(e) =>
                updateLine(l.id, {
                  groupSize: Number(e.target.value),
                })
              }
              className="h-7 w-20"
            />
          </div>
        )}
        {l.product.kind === "family" && (
          <div className="flex items-center gap-2 text-xs">
            <Label className="text-xs">Enfants suppl.</Label>
            <Input
              type="number"
              min={0}
              value={l.extraChildren}
              disabled={locked}
              onChange={(e) =>
                updateLine(l.id, {
                  extraChildren: Number(e.target.value),
                })
              }
              className="h-7 w-20"
            />
          </div>
        )}
        {(l.product.kind === "simple" || l.product.kind === "pass") && (
          <div className="grid grid-cols-3 gap-1">
            {ALL_TARIFFS.map((t) => (
              <div
                key={t}
                className="flex items-center justify-between rounded-md border px-1.5 py-1"
              >
                <span
                  className={cn(
                    "text-[11px] leading-none",
                    FREE_TARIFFS.has(t) && "text-muted-foreground"
                  )}
                >
                  {TARIFF_SHORT[t]}
                </span>
                <span className="flex items-center gap-0.5">
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-5"
                    disabled={locked || l.counts[t] === 0}
                    onClick={() => bumpCount(l.id, t, -1)}
                  >
                    <Minus className="size-3" />
                  </Button>
                  <span className="w-4 text-center text-xs tabular-nums">
                    {l.counts[t]}
                  </span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-5"
                    disabled={locked}
                    onClick={() => bumpCount(l.id, t, 1)}
                  >
                    <Plus className="size-3" />
                  </Button>
                </span>
              </div>
            ))}
          </div>
        )}

        {need > 0 &&
          Array.from({ length: need }).map((_, i) => (
            <select
              key={i}
              value={l.sessionIds[i] ?? ""}
              disabled={locked}
              onChange={(e) => {
                const next = [...l.sessionIds];
                next[i] = e.target.value;
                updateLine(l.id, { sessionIds: next });
              }}
              className="h-8 w-full rounded-md border bg-background px-2 text-xs"
            >
              <option value="">— Séance {i + 1} —</option>
              {sessions.map((s) => (
                <option
                  key={s.id}
                  value={s.id}
                  disabled={
                    s.remaining === 0 ||
                    s.expired ||
                    (l.sessionIds.includes(s.id) &&
                      l.sessionIds[i] !== s.id)
                  }
                >
                  {s.label} — {s.show} (
                  {s.expired ? "terminée" : `${s.remaining} pl.`})
                </option>
              ))}
            </select>
          ))}
        {err && <p className="text-xs text-red-600">{err}</p>}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 print:hidden">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Caisse</h1>
          <p className="text-sm text-muted-foreground">
            Guichet — vente et encaissement multi-moyens.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Label htmlFor="pos-date" className="text-sm">
            Jour de visite
          </Label>
          <Input
            id="pos-date"
            type="date"
            value={visitDate}
            disabled={locked}
            onChange={(e) => changeDate(e.target.value)}
            className="w-40"
          />
          {highSeason && (
            <span className="rounded-full bg-orange-100 px-2.5 py-1 text-xs font-medium text-orange-700">
              Haute saison
            </span>
          )}
          {order?.status === "pending" && (
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={cancelOrder}
            >
              <Pencil className="size-4" />
              Modifier la commande
            </Button>
          )}
          {order && (
            <Button variant="outline" size="sm" onClick={reset}>
              <RotateCcw className="size-4" />
              Nouvelle vente
            </Button>
          )}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1.1fr_1fr_1fr] print:hidden">
        {/* ------------------------------ Produits ------------------------- */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Produits</CardTitle>
            <CardDescription>
              Ajoutez des articles au panier.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {products.map((p) => (
              <button
                key={p.code}
                type="button"
                disabled={locked}
                onClick={() =>
                  p.is_addon ? addExtraShow(p) : addLine(p)
                }
                className="flex w-full items-center justify-between rounded-lg border px-3 py-2 text-left text-sm transition-colors hover:bg-muted disabled:opacity-50"
              >
                <span>
                  <span className="block font-medium">{p.label}</span>
                  <span className="text-xs text-muted-foreground">
                    {p.kind === "group" ? "groupe (8+)" : p.kind}
                  </span>
                </span>
                <Plus className="size-4 text-muted-foreground" />
              </button>
            ))}
          </CardContent>
        </Card>

        {/* ------------------------------- Panier -------------------------- */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Panier</CardTitle>
            <CardDescription>
              {lines.length} article{lines.length > 1 ? "s" : ""}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {lines.length === 0 && (
              <p className="py-6 text-center text-sm text-muted-foreground">
                Panier vide — ajoutez des produits.
              </p>
            )}
            {regularLines.map(renderLine)}

            {passHint && (
              <div className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 dark:border-amber-700 dark:bg-amber-950/40">
                <p className="text-xs font-medium">
                  Pass 1 Spectacle possible pour {passHint.persons}{" "}
                  personne{passHint.persons > 1 ? "s" : ""} — économie{" "}
                  {eurFormatter.format(passHint.savings)}
                </p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  Musée + séance vendus à l&apos;unité dans le panier.
                </p>
                <Button
                  size="sm"
                  variant="outline"
                  className="mt-1.5 h-7 text-xs"
                  onClick={convertToPass}
                >
                  Convertir en Pass 1 Spectacle
                </Button>
              </div>
            )}

            {extraLines.length > 0 && (
              <div className="rounded-lg border">
                <button
                  type="button"
                  onClick={() => setExtraOpen((v) => !v)}
                  className="flex w-full items-center justify-between px-3 py-2 text-left text-sm"
                >
                  <span className="font-medium">
                    Séances supplémentaires ×{extraLines.length}
                    {extraErrCount > 0 && (
                      <span className="ml-2 font-normal text-red-600">
                        {extraErrCount} à compléter
                      </span>
                    )}
                  </span>
                  <span className="flex items-center gap-2">
                    <span className="tabular-nums">
                      {eurFormatter.format(extraTotal)}
                    </span>
                    <ChevronDown
                      className={cn(
                        "size-4 text-muted-foreground transition-transform",
                        extraOpen && "rotate-180"
                      )}
                    />
                  </span>
                </button>
                {extraOpen && (
                  <div className="space-y-3 border-t p-3">
                    {extraLines.map(renderLine)}
                  </div>
                )}
              </div>
            )}

            {lines.length > 0 && !locked && (
              <>
                <div className="space-y-1.5">
                  <Label htmlFor="pos-email" className="text-xs">
                    Email client (optionnel)
                  </Label>
                  <Input
                    id="pos-email"
                    type="email"
                    placeholder="guichet@musee-pirates.fr"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                  />
                </div>
                <div className="flex items-center justify-between rounded-lg bg-muted/50 px-3 py-2">
                  <span className="text-sm font-medium">Total estimé</span>
                  <span className="font-bold tabular-nums">
                    {eurFormatter.format(estimate)}
                  </span>
                </div>
                <Button
                  className="w-full"
                  disabled={busy || lines.some((l) => lineErrors(l) !== null)}
                  onClick={submitOrder}
                >
                  {busy && <Loader2 className="size-4 animate-spin" />}
                  Valider la commande
                </Button>
              </>
            )}
          </CardContent>
        </Card>

        {/* ---------------------------- Encaissement ----------------------- */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Encaissement</CardTitle>
            <CardDescription>
              {order
                ? `Commande ${order.id.slice(0, 8)}…`
                : "Validez le panier pour encaisser."}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-3 gap-2 text-center">
              <div className="rounded-lg bg-muted/50 px-2 py-2.5">
                <p className="text-xs text-muted-foreground">Total</p>
                <p className="font-bold tabular-nums">
                  {order ? eurFormatter.format(order.total) : "—"}
                </p>
              </div>
              <div className="rounded-lg bg-muted/50 px-2 py-2.5">
                <p className="text-xs text-muted-foreground">Encaissé</p>
                <p className="font-bold tabular-nums text-green-700">
                  {order ? eurFormatter.format(order.paid) : "—"}
                </p>
              </div>
              <div
                className={cn(
                  "rounded-lg px-2 py-2.5",
                  order && order.due === 0
                    ? "bg-green-100"
                    : "bg-muted/50"
                )}
              >
                <p className="text-xs text-muted-foreground">Reste</p>
                <p className="font-bold tabular-nums">
                  {order ? eurFormatter.format(order.due) : "—"}
                </p>
              </div>
            </div>

            {order && order.status !== "confirmed" && (
              <>
                <div className="grid grid-cols-4 gap-1.5">
                  {PAYMENT_METHODS.map(({ key, label, icon: Icon }) => {
                    const disabledCheck = key === "check" && !hasGroup;
                    return (
                      <button
                        key={key}
                        type="button"
                        disabled={disabledCheck}
                        title={
                          disabledCheck
                            ? "Réservé aux ventes groupes/scolaires"
                            : undefined
                        }
                        onClick={() => setMethod(key)}
                        className={cn(
                          "flex flex-col items-center gap-1 rounded-lg border px-2 py-2 text-xs transition-colors",
                          method === key
                            ? "border-primary bg-primary/10 font-medium"
                            : "hover:bg-muted",
                          disabledCheck && "cursor-not-allowed opacity-40"
                        )}
                      >
                        <Icon className="size-4" />
                        {label}
                      </button>
                    );
                  })}
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="pos-amount" className="text-xs">
                    Montant remis
                  </Label>
                  <div className="flex gap-1.5">
                    <Input
                      id="pos-amount"
                      type="number"
                      min="0"
                      step="0.01"
                      value={amountStr}
                      onChange={(e) => setAmountStr(e.target.value)}
                      placeholder="0.00"
                    />
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        setAmountStr(order.due.toFixed(2))
                      }
                    >
                      Solde
                    </Button>
                  </div>
                  {method === "cash" && (
                    <div className="flex flex-wrap gap-1">
                      {[5, 10, 20, 50, 100].map((v) => (
                        <Button
                          key={v}
                          type="button"
                          variant="outline"
                          size="sm"
                          className="h-7 px-2 text-xs"
                          onClick={() =>
                            setAmountStr(
                              ((Number(amountStr) || 0) + v).toFixed(2)
                            )
                          }
                        >
                          +{v} €
                        </Button>
                      ))}
                    </div>
                  )}
                </div>

                {previewChange > 0 && (
                  <div className="rounded-lg bg-amber-50 px-3 py-2 text-sm">
                    Rendu monnaie :{" "}
                    <span className="font-bold">
                      {eurFormatter.format(previewChange)}
                    </span>
                  </div>
                )}
                {ancvOverflow > 0 && (
                  <div className="rounded-lg bg-amber-50 px-3 py-2 text-xs">
                    ANCV supérieur au solde :{" "}
                    {eurFormatter.format(ancvOverflow)} d&apos;excédent —
                    aucun rendu (DFC n°7).
                  </div>
                )}

                <Button
                  className="w-full"
                  disabled={busy || amount <= 0}
                  onClick={pay}
                >
                  {busy && <Loader2 className="size-4 animate-spin" />}
                  Encaisser {amount > 0 && eurFormatter.format(amount)}
                </Button>
              </>
            )}

            {payments.length > 0 && (
              <div className="space-y-1">
                <p className="text-xs font-medium text-muted-foreground">
                  Transactions
                </p>
                {payments.map((p, i) => (
                  <div
                    key={i}
                    className="flex items-center justify-between rounded border px-2 py-1 text-xs"
                  >
                    <span className="uppercase">{p.method}</span>
                    <span className="tabular-nums">
                      {eurFormatter.format(p.amount)}
                      {p.amount !== p.applied &&
                        ` → ${eurFormatter.format(p.applied)}`}
                      {p.change > 0 && ` (rendu ${eurFormatter.format(p.change)})`}
                    </span>
                  </div>
                ))}
              </div>
            )}

            {order && (
              <Button
                className="w-full"
                variant={order.status === "confirmed" ? "default" : "outline"}
                disabled={order.status !== "confirmed"}
                onClick={() => setShowTickets((v) => !v)}
              >
                <Printer className="size-4" />
                Émettre les billets
              </Button>
            )}
          </CardContent>
        </Card>
      </div>

      {showTickets && tickets.length > 0 && (
        <Card className="print:gap-0 print:py-0 print:ring-0">
          <CardHeader className="pb-3 print:hidden">
            <CardTitle className="text-base">
              {tickets.length} billet{tickets.length > 1 ? "s" : ""} émis
            </CardTitle>
            <CardAction>
              <PrintTicketsButton label="Imprimer" />
            </CardAction>
          </CardHeader>
          <CardContent className="print:px-0">
            <div className="flex flex-wrap gap-4">
              {tickets.map((t) => (
                <TicketCard
                  key={t.id}
                  ticket={t}
                  reservationRef={order?.id}
                />
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
