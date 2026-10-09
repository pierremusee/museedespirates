"use client";

import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
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
import {
  ALL_TARIFFS,
  FREE_PROFILE_TARIFFS,
  FREE_TARIFFS,
  MIN_GROUP_SIZE,
  PAID_TARIFFS,
  PASS_1_CODE,
  absorbIntoPass,
  addProductToCart,
  autoSessionExcluding,
  autoSessions,
  countsOf,
  hasMuseumDay,
  lineEstimate,
  linePersons,
  passSuggestion,
  sessionCountOf,
  type CartLine,
  type Product,
  type SessionOption,
  type Tariff,
} from "./cart";
import {
  clearPosPending,
  posResolution,
  readPosPending,
  savePosPending,
  type PosPendingPayment,
} from "./pos-pending";

// ---------------------------------------------------------------------------
// Types locaux (UI / encaissement)
// ---------------------------------------------------------------------------

type PaymentMethod = "cb" | "cash" | "ancv" | "check";

type OrderState = {
  id: string;
  total: number;
  paid: number;
  due: number;
  status: "pending" | "confirmed" | "cancelled" | "expired";
};

// Réponse de GET /reservations/{id} — lecture de restauration.
type ReservationDetail = {
  id: string;
  status: string;
  total_price: string;
  paid_amount: string;
  amount_due: string;
  payments?: { method: string; amount: string; applied_amount: string }[];
  tickets?: TicketRead[];
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
  // Verrou synchrone : `busy` (state React) laisse passer un double
  // clic dans le même batch — le ref bloque la rentrée immédiatement.
  const paymentInFlight = useRef(false);
  // Opération d'encaissement incertaine : clé + paramètres figés tant
  // que le serveur n'a pas tranché. State (pas ref) car elle pilote le
  // rendu ; persistée en sessionStorage pour survivre au rechargement.
  const [pendingOp, setPendingOp] = useState<PosPendingPayment | null>(
    null
  );
  // Opération restaurée dont l'état n'a pas pu être établi : la caisse
  // reste bloquée jusqu'à vérification — jamais de nouvelle vente
  // silencieuse alors qu'un encaissement (espèces notamment) a pu avoir
  // lieu au guichet.
  const [restoreBlocked, setRestoreBlocked] =
    useState<PosPendingPayment | null>(null);
  const [restoreTick, setRestoreTick] = useState(0);

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

  // Restaure localement une commande lue via GET /reservations/{id}
  // (reprise après rechargement de la page de caisse).
  function applyReservation(detail: ReservationDetail) {
    setOrder({
      id: detail.id,
      total: Number(detail.total_price),
      paid: Number(detail.paid_amount),
      due: Number(detail.amount_due),
      status: detail.status as OrderState["status"],
    });
    setPayments(
      (detail.payments ?? []).map((p) => ({
        method: p.method as PaymentMethod,
        amount: Number(p.amount),
        applied: Number(p.applied_amount),
        // Le rendu n'est pas renvoyé par le GET : reconstitué pour les
        // espèces (nominal − imputé), nul sinon — affichage seul.
        change:
          p.method === "cash"
            ? Number(p.amount) - Number(p.applied_amount)
            : 0,
      }))
    );
    if (detail.tickets) setTickets(detail.tickets);
  }

  // Rechargement avec un encaissement incertain persisté : vérifier la
  // commande AVANT d'autoriser toute nouvelle vente. `restoreTick`
  // permet au bouton « Revérifier » de relancer la résolution.
  useEffect(() => {
    const stored = readPosPending(sessionStorage);
    if (!stored) return;
    void (async () => {
      let httpOk = false;
      let detail: ReservationDetail | null = null;
      try {
        const chk = await fetch(
          `${API}/reservations/${stored.reservationId}`,
          { cache: "no-store" }
        );
        httpOk = chk.ok;
        if (chk.ok) detail = await chk.json();
      } catch {
        // Injoignable → "blocked" ci-dessous.
      }
      const action = posResolution(httpOk, detail?.status ?? null);
      if (action === "blocked") {
        setRestoreBlocked(stored);
        toast.error(
          "Un encaissement précédent n'a pas pu être vérifié — la caisse est bloquée jusqu'à résolution."
        );
        return;
      }
      setRestoreBlocked(null);
      if (action === "conclude") {
        // Le paiement avait abouti : la commande confirmée est
        // restaurée (billets récupérables), la clé est libérée.
        clearPosPending(sessionStorage);
        if (detail) {
          applyReservation(detail);
          setShowTickets(true);
        }
        toast.success(
          "L'encaissement en attente avait abouti — commande confirmée restaurée."
        );
        return;
      }
      if (action === "resume" && detail) {
        // Commande toujours payable : la même opération (même clé,
        // mêmes paramètres) peut être reprise — rejouée côté serveur
        // si l'encaissement avait été enregistré.
        applyReservation(detail);
        setPendingOp(stored);
        toast.info(
          "Encaissement incertain restauré — reprenez-le avec les paramètres figés (clé conservée)."
        );
        return;
      }
      // abandon : commande annulée/expirée — clé libérée ; les
      // encaissements éventuels restent tracés côté serveur
      // (remboursement manuel si nécessaire).
      clearPosPending(sessionStorage);
      const n = detail?.payments?.length ?? 0;
      toast.info(
        n > 0
          ? `Commande ${detail?.status} — ${n} encaissement(s) déjà enregistré(s) : remboursement manuel requis.`
          : "L'opération précédente est abandonnée (commande inactive)."
      );
    })();
  }, [restoreTick]);

  // -------------------------------------------------------------------------
  // Panier
  // -------------------------------------------------------------------------

  function addLine(product: Product) {
    // Délègue au moteur panier (./cart) : fusion +1 sur même produit,
    // composition miroir, conversion automatique en Pass sur correspondance
    // stricte, ou absorption des lignes simples au clic sur le Pass.
    const res = addProductToCart(
      lines,
      product,
      products,
      sessions,
      nextId
    );
    setLines(res.lines);
    setNextId(res.nextId);
    if (res.optimized) {
      const eco = eurFormatter.format(res.optimized.savings);
      toast.success(
        res.absorbed
          ? `Lignes Musée + Théâtre regroupées dans le Pass — économie ${eco}.`
          : `Panier optimisé : Pass 1 Spectacle ×${res.optimized.persons} — économie ${eco}.`,
        { duration: 6000 }
      );
    }
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
  // Suggestion « Pass 1 Spectacle » (cas partiels/ambigus — la correspondance
  // stricte est déjà convertie automatiquement à l'ajout, cf. ./cart)
  // -------------------------------------------------------------------------

  function convertToPass() {
    const hint = passSuggestion(lines, products);
    const pass = products.find((p) => p.code === PASS_1_CODE);
    if (!hint || !pass) return;
    setLines(absorbIntoPass(lines, hint, pass, nextId));
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
    if (pendingOp || restoreBlocked) {
      // Un encaissement incertain ne doit jamais coexister avec une
      // nouvelle vente : résoudre l'opération d'abord.
      toast.error(
        "Un encaissement est en attente de résolution — terminez-le avant une nouvelle vente."
      );
      return;
    }
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
    if (!order || paymentInFlight.current) return;
    // Montant figé sur l'opération en attente : les champs du formulaire
    // sont ignorés tant que la tranche précédente n'est pas résolue.
    const amount = pendingOp ? Number(pendingOp.amount) : Number(amountStr) || 0;
    if (!pendingOp && amount <= 0) return;
    paymentInFlight.current = true;
    setBusy(true);
    // Idempotence : une opération dont l'issue est incertaine reprend
    // sa clé et ses paramètres figés ; une nouvelle opération reçoit
    // une clé neuve. Persistée pour survivre au rechargement.
    const op =
      pendingOp ?? {
        reservationId: order.id,
        key: crypto.randomUUID(),
        method,
        amount: amount.toFixed(2),
      };
    setPendingOp(op);
    savePosPending(op, sessionStorage);
    try {
      const res = await fetch(
        `${API}/reservations/${op.reservationId}/payments`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            method: op.method,
            amount: op.amount,
            idempotency_key: op.key,
          }),
        }
      );
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        if (res.status >= 500) {
          // Erreur serveur : issue incertaine — la clé est conservée,
          // le prochain essai reprendra cette même opération.
          toast.error(
            "Erreur serveur : le résultat de l'encaissement est incertain — réessayez."
          );
          return;
        }
        // Rejet métier certain (4xx) : l'opération est close, la clé
        // est libérée ; la commande est relue pour refléter son état
        // réel (elle a pu être annulée ou expirer entre-temps).
        setPendingOp(null);
        clearPosPending(sessionStorage);
        try {
          const chk = await fetch(`${API}/reservations/${order.id}`, {
            cache: "no-store",
          });
          if (chk.ok) applyReservation(await chk.json());
        } catch {
          // Statut local conservé — l'erreur métier reste affichée.
        }
        toast.error(body.detail ?? `Erreur ${res.status} au paiement.`);
        return;
      }
      setPendingOp(null);
      clearPosPending(sessionStorage);
      const change = Number(body.change_due);
      setPayments((prev) => [
        ...prev,
        {
          method: op.method,
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
      // Erreur réseau/timeout : le serveur a peut-être enregistré
      // l'opération — clé et paramètres conservés pour la reprise.
      toast.error(
        "Connexion perdue : résultat de l'encaissement incertain — réessayez avant de modifier le montant."
      );
    } finally {
      paymentInFlight.current = false;
      setBusy(false);
    }
  }

  async function cancelOrder() {
    // Annulation interdite tant qu'un encaissement est incertain : la
    // tranche a peut-être été enregistrée — il faut d'abord la résoudre.
    if (!order || order.status !== "pending" || busy || pendingOp)
      return;
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
      setPendingOp(null);
      clearPosPending(sessionStorage);
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
    if (pendingOp || restoreBlocked) {
      toast.error(
        "Encaissement en attente — résolvez-le avant de repartir sur une nouvelle vente."
      );
      return;
    }
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
  const passHint = locked ? null : passSuggestion(lines, products);

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
            {l.optimized && (
              <span className="ml-1.5 rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
                Optimisé
              </span>
            )}
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
              disabled={busy || pendingOp !== null}
              onClick={cancelOrder}
            >
              <Pencil className="size-4" />
              Modifier la commande
            </Button>
          )}
          {order && (
            <Button
              variant="outline"
              size="sm"
              disabled={busy || pendingOp !== null}
              onClick={reset}
            >
              <RotateCcw className="size-4" />
              Nouvelle vente
            </Button>
          )}
        </div>
      </div>

      {restoreBlocked && (
        <div className="flex items-start gap-3 rounded-lg border border-red-300 bg-red-50 px-4 py-3 dark:border-red-800 dark:bg-red-950/40">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-red-700 dark:text-red-300" />
          <div className="space-y-1.5 text-sm">
            <p className="font-medium">
              Encaissement non vérifié :{" "}
              {PAYMENT_METHODS.find((m) => m.key === restoreBlocked.method)
                ?.label ?? restoreBlocked.method}{" "}
              {eurFormatter.format(Number(restoreBlocked.amount))} —
              commande {restoreBlocked.reservationId.slice(0, 8)}…
            </p>
            <p className="text-xs text-muted-foreground">
              Le résultat de cet encaissement n&apos;a pas pu être établi.
              La caisse reste bloquée : aucune nouvelle vente ne peut
              démarrer tant que la commande n&apos;est pas vérifiée.
            </p>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={() => setRestoreTick((t) => t + 1)}
            >
              Revérifier la commande
            </Button>
          </div>
        </div>
      )}

      {!restoreBlocked && (
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

            {order && order.status !== "confirmed" && pendingOp && (
              <>
                {/* Tranche incertaine : paramètres figés, même clé au
                    réessai — jamais de modification avant résolution. */}
                <div className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm dark:border-amber-700 dark:bg-amber-950/40">
                  <p className="font-medium">
                    Encaissement en attente :{" "}
                    {PAYMENT_METHODS.find((m) => m.key === pendingOp.method)
                      ?.label ?? pendingOp.method}{" "}
                    {eurFormatter.format(Number(pendingOp.amount))}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    Résultat incertain — les paramètres sont figés.
                    Réessayez l&apos;encaissement ; il sera rejoué ou
                    enregistré une seule fois.
                  </p>
                </div>
                <Button
                  className="w-full"
                  disabled={busy}
                  onClick={pay}
                >
                  {busy && <Loader2 className="size-4 animate-spin" />}
                  Réessayer l&apos;encaissement
                </Button>
              </>
            )}

            {order && order.status !== "confirmed" && !pendingOp && (
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
      )}

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
