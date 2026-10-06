"use client";

import { useState } from "react";
import {
  Banknote,
  ChevronDown,
  CreditCard,
  FileText,
  Loader2,
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
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { TicketQR } from "@/app/(client)/succes/ticket-qr";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type SessionOption = { id: string; label: string; remaining: number };

type Product = {
  code: string;
  label: string;
  kind: "simple" | "pass" | "family" | "group";
  price_adult: string | null;
  price_child: string | null;
  price_reduced: string | null;
  family_base_price: string | null;
  extra_child_price: string | null;
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

type CartLine = {
  id: number;
  product: Product;
  tariff: Tariff;
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

const TARIFF_LABELS: Record<Tariff, string> = {
  adult: "Adulte",
  child: "Enfant",
  reduced: "Réduit",
  under_4: "-4 ans (gratuit)",
  disability: "Invalidité (gratuit)",
  pmr_companion: "PMR (gratuit)",
};

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

const TICKET_TYPE_LABELS: Record<string, string> = {
  open_ticket: "Musée",
  session_standard: "Théâtre",
  session_dining: "Dîner-spectacle",
};

const eurFormatter = new Intl.NumberFormat("fr-FR", {
  style: "currency",
  currency: "EUR",
});

const dateTimeFormatter = new Intl.DateTimeFormat("fr-FR", {
  dateStyle: "short",
  timeStyle: "short",
  timeZone: "Europe/Paris",
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

function lineEstimate(line: CartLine, highSeason: boolean): number {
  const p = line.product;
  if (line.tariff !== undefined && FREE_TARIFFS.has(line.tariff)) return 0;
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
  const priceKey = `price_${line.tariff}` as
    | "price_adult"
    | "price_child"
    | "price_reduced";
  const mod = line.tariff === "child" ? MOD_CHILD : MOD_ADULT_REDUCED;
  return Number(p[priceKey]) + (highSeason ? mod : 0);
}

function autoSessions(
  product: Product,
  sessions: SessionOption[]
): string[] {
  const need = sessionCountOf(product);
  if (need === 0) return [];
  const available = sessions.filter((s) => s.remaining > 0).map((s) => s.id);
  const picks = available.slice(0, need);
  while (picks.length < need) picks.push("");
  return picks;
}

function autoSessionExcluding(
  sessions: SessionOption[],
  exclude: string[]
): string {
  return (
    sessions.find((s) => s.remaining > 0 && !exclude.includes(s.id))?.id ?? ""
  );
}

const EXTRA_SHOW_CODE = "extra_show";

function personsOf(line: CartLine): { tariff: Tariff; exclude: string[] }[] {
  const exclude = line.sessionIds.filter(Boolean);
  if (line.product.kind === "family") {
    const members: Tariff[] = ["adult", "adult"];
    for (let i = 0; i < 2 + line.extraChildren; i++)
      members.push("child");
    return members.map((tariff) => ({ tariff, exclude }));
  }
  if (line.product.kind === "group") {
    return Array.from({ length: line.groupSize }, () => ({
      tariff: "adult" as Tariff,
      exclude,
    }));
  }
  return [{ tariff: line.tariff, exclude }];
}

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
  const regularLines = lines.filter(
    (l) => l.product.code !== EXTRA_SHOW_CODE
  );
  const extraLines = lines.filter(
    (l) => l.product.code === EXTRA_SHOW_CODE
  );
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
    setLines((prev) => [
      ...prev,
      {
        id: nextId,
        product,
        tariff: "adult",
        groupSize: MIN_GROUP_SIZE,
        extraChildren: 0,
        sessionIds: autoSessions(product, sessions),
      },
    ]);
    setNextId((n) => n + 1);
  }

  function addExtraShow(product: Product) {
    const persons = lines
      .filter((l) => l.product.code !== EXTRA_SHOW_CODE)
      .flatMap(personsOf);
    if (persons.length === 0) {
      addLine(product);
      return;
    }
    setLines((prev) => [
      ...prev,
      ...persons.map((p, i) => ({
        id: nextId + i,
        product,
        tariff: p.tariff,
        groupSize: MIN_GROUP_SIZE,
        extraChildren: 0,
        sessionIds: [autoSessionExcluding(sessions, p.exclude)],
      })),
    ]);
    setNextId((n) => n + persons.length);
    toast.success(
      `${persons.length} séance(s) supplémentaire(s) ajoutée(s) — retirez celles non souhaitées.`
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
        event_type: string;
        sessions: {
          id: string;
          start_time: string;
          remaining_capacity: number;
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
            label: timeFmt.format(new Date(s.start_time)),
            remaining: s.remaining_capacity,
          }))
        )
        .sort((a, b) => a.label.localeCompare(b.label));
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
    }
    if (
      line.product.kind === "group" &&
      line.groupSize < MIN_GROUP_SIZE
    ) {
      return `groupe : ${MIN_GROUP_SIZE} pers. minimum`;
    }
    return null;
  }

  function buildItems() {
    return lines.map((l) => ({
      product_code: l.product.code,
      ...(l.product.kind === "family"
        ? { extra_children: l.extraChildren }
        : l.product.kind === "group"
          ? { group_size: l.groupSize }
          : FREE_TARIFFS.has(l.tariff)
            ? {
                free_profile: l.tariff,
                ...(l.tariff === "disability" ? { category: "adult" } : {}),
              }
            : { category: l.tariff }),
      ...(hasMuseumDay(l.product) ? { visit_date: visitDate } : {}),
      ...(sessionCountOf(l.product) > 0
        ? { session_ids: l.sessionIds.filter(Boolean) }
        : {}),
    }));
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
        {l.product.kind !== "family" &&
          l.product.kind !== "group" && (
            <select
              value={l.tariff}
              disabled={locked}
              onChange={(e) =>
                updateLine(l.id, {
                  tariff: e.target.value as Tariff,
                })
              }
              className="h-8 w-full rounded-md border bg-background px-2 text-xs"
            >
              {(Object.keys(TARIFF_LABELS) as Tariff[]).map((t) => (
                <option key={t} value={t}>
                  {TARIFF_LABELS[t]}
                </option>
              ))}
            </select>
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
                    (l.sessionIds.includes(s.id) &&
                      l.sessionIds[i] !== s.id)
                  }
                >
                  {s.label} ({s.remaining} pl.)
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
      <div className="flex flex-wrap items-center justify-between gap-3">
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

      <div className="grid gap-4 lg:grid-cols-[1.1fr_1fr_1fr]">
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
                  p.code === EXTRA_SHOW_CODE ? addExtraShow(p) : addLine(p)
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
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">
              {tickets.length} billet{tickets.length > 1 ? "s" : ""} émis
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-4">
              {tickets.map((t) => (
                <div
                  key={t.id}
                  className="flex flex-col items-center gap-2 rounded-lg border p-3"
                >
                  <div className="rounded bg-white p-1.5">
                    <TicketQR value={t.id} />
                  </div>
                  <p className="text-center text-xs">
                    {t.ticket_category}
                    {t.accesses.map((a) => (
                      <span
                        key={a.id}
                        className="block text-muted-foreground"
                      >
                        {TICKET_TYPE_LABELS[a.access_type] ?? a.access_type}
                        {a.session_start
                          ? ` — ${dateTimeFormatter.format(
                              new Date(a.session_start)
                            )}`
                          : a.valid_date
                            ? ` — ${new Date(
                                `${a.valid_date}T12:00:00`
                              ).toLocaleDateString("fr-FR")}`
                            : ""}
                      </span>
                    ))}
                  </p>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
