"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { CalendarClock, Loader2, Minus, Plus, Ticket } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import {
  clearPendingPayment,
  pendingAction,
  previousOutcome,
  readPendingPayment,
  savePendingPayment,
  type PendingPayment,
} from "./pending-payment";

type Category = "adult" | "child" | "reduced";
type FreeProfile = "under_4" | "disability" | "pmr_companion";

type SessionOption = {
  id: string;
  label: string;
  show: string;
  remaining: number;
};

type ProductProps = {
  code: string;
  label: string;
  kind: "simple" | "pass" | "family" | "group";
  price_adult: string | null;
  price_child: string | null;
  price_reduced: string | null;
  family_base_price: string | null;
  extra_child_price: string | null;
};

const PAID_ROWS: { key: Category; label: string; priceKey: keyof ProductProps }[] =
  [
    { key: "adult", label: "Adulte", priceKey: "price_adult" },
    { key: "child", label: "Enfant (-12 ans)", priceKey: "price_child" },
    { key: "reduced", label: "Tarif réduit*", priceKey: "price_reduced" },
  ];

const FREE_ROWS: { key: string; label: string; hint: string }[] = [
  { key: "under_4", label: "Moins de 4 ans*", hint: "gratuit" },
  { key: "disability", label: "Carte d'invalidité*", hint: "gratuit" },
  { key: "pmr_companion", label: "Accompagnateur PMR*", hint: "gratuit" },
];

type Counts = {
  adult: number;
  child: number;
  reduced: number;
  under_4: number;
  disability: number;
  pmr_companion: number;
};

const ZERO: Counts = {
  adult: 0,
  child: 0,
  reduced: 0,
  under_4: 0,
  disability: 0,
  pmr_companion: 0,
};

const eurFormatter = new Intl.NumberFormat("fr-FR", {
  style: "currency",
  currency: "EUR",
});

// Modificateurs haute saison (DFC n°5 §4).
const MOD_ADULT_REDUCED = 2;
const MOD_CHILD = 1;
const MOD_FAMILY = 5;

export function ReservationDialog({
  product,
  extraProduct,
  sessions,
  visitDate,
  highSeason,
  sessionCount,
  hasMuseumDay,
  soldOut,
}: {
  product: ProductProps;
  extraProduct: ProductProps | null;
  sessions: SessionOption[];
  visitDate: string;
  highSeason: boolean;
  sessionCount: number;
  hasMuseumDay: boolean;
  soldOut: boolean;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [counts, setCounts] = useState<Counts>({ ...ZERO, adult: 1 });
  const [extraChildren, setExtraChildren] = useState(0);
  const [selectedSessions, setSelectedSessions] = useState<string[]>([]);
  const [extraSessionId, setExtraSessionId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // Verrou synchrone : `submitting` (state) laisse passer un double
  // submit dans le même batch React — le ref bloque immédiatement.
  const submitInFlight = useRef(false);
  // Paiement à l'issue incertaine en attente (même réservation, même
  // clé d'idempotence) — miroir de sessionStorage, cf pending-payment.ts.
  const pendingRef = useRef<PendingPayment | null>(null);
  // Opération incertaine affichée : tant qu'elle existe pour CE produit,
  // le formulaire est figé — l'estimation ne doit pas se substituer au
  // montant réellement engagé. Chargée à l'ouverture (sessionStorage
  // est indisponible côté serveur).
  const [pendingUI, setPendingUI] = useState<PendingPayment | null>(null);

  useEffect(() => {
    if (!open) return;
    const p = pendingRef.current ?? readPendingPayment(sessionStorage);
    setPendingUI(
      p && pendingAction(p, product.code) === "resume" ? p : null
    );
  }, [open, product.code]);

  const isFamily = product.kind === "family";
  const needsSessions = sessionCount > 0;

  // Capacité max par personne : min des places restantes des séances choisies.
  const capacity = needsSessions
    ? selectedSessions.length === sessionCount
      ? Math.min(
          ...selectedSessions.map(
            (id) => sessions.find((s) => s.id === id)?.remaining ?? 0
          )
        )
      : 0
    : 99;

  const paidTickets = counts.adult + counts.child + counts.reduced;
  const freeTickets = counts.under_4 + counts.disability + counts.pmr_companion;
  const persons = isFamily
    ? 4 + extraChildren
    : paidTickets + freeTickets;

  // Estimation du total côté client (le serveur recalcule — indicatif).
  let estimate = 0;
  if (isFamily) {
    estimate =
      Number(product.family_base_price) +
      extraChildren * Number(product.extra_child_price) +
      (highSeason ? MOD_FAMILY : 0);
  } else {
    estimate +=
      counts.adult * (Number(product.price_adult) + (highSeason ? MOD_ADULT_REDUCED : 0));
    estimate +=
      counts.reduced * (Number(product.price_reduced) + (highSeason ? MOD_ADULT_REDUCED : 0));
    estimate +=
      counts.child * (Number(product.price_child) + (highSeason ? MOD_CHILD : 0));
  }

  // Séance supplémentaire à tarif réduit : même tarif par catégorie
  // que les personnes de la commande (gratuit pour les profils gratuits).
  if (extraSessionId && extraProduct) {
    const exAdult =
      Number(extraProduct.price_adult) + (highSeason ? MOD_ADULT_REDUCED : 0);
    const exReduced =
      Number(extraProduct.price_reduced) + (highSeason ? MOD_ADULT_REDUCED : 0);
    const exChild =
      Number(extraProduct.price_child) + (highSeason ? MOD_CHILD : 0);
    if (isFamily) {
      estimate += 2 * exAdult + (2 + extraChildren) * exChild;
    } else {
      estimate +=
        counts.adult * exAdult +
        counts.reduced * exReduced +
        counts.child * exChild;
    }
  }

  function updateCount(key: keyof Counts, delta: number) {
    setCounts((prev) => ({
      ...prev,
      [key]: Math.max(0, Math.min(capacity, prev[key] + delta)),
    }));
  }

  function toggleSession(id: string) {
    setSelectedSessions((prev) => {
      if (prev.includes(id)) return prev.filter((s) => s !== id);
      if (prev.length >= sessionCount) return prev;
      return [...prev, id];
    });
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitInFlight.current) return;
    // Opération à l'issue incertaine persistée (mémoire, puis
    // sessionStorage pour survivre au rechargement) — lue AVANT les
    // validations : la reprise rejoue le paiement de la réservation
    // existante, les paramètres du panier ne sont pas revalidés.
    const stored = pendingRef.current ?? readPendingPayment(sessionStorage);
    const resuming =
      stored !== null && pendingAction(stored, product.code) === "resume";
    if (!resuming) {
      if (needsSessions && selectedSessions.length !== sessionCount) {
        toast.error(
          `Sélectionnez ${sessionCount} séance${sessionCount > 1 ? "s" : ""}.`
        );
        return;
      }
      if (persons === 0) {
        toast.error("Sélectionnez au moins un billet.");
        return;
      }
      if (needsSessions && persons > capacity) {
        toast.error("Pas assez de places disponibles sur cette séance.");
        return;
      }
      if (extraSessionId === "") {
        toast.error("Choisissez la séance supplémentaire.");
        return;
      }
      if (extraSessionId) {
        const extra = sessions.find((s) => s.id === extraSessionId);
        if (!extra || extra.remaining < persons) {
          toast.error(
            "Pas assez de places disponibles sur la séance supplémentaire."
          );
          return;
        }
      }
    }

    setSubmitting(true);
    submitInFlight.current = true;
    try {
      const api = process.env.NEXT_PUBLIC_API_URL;
      let pending = stored;

      if (
        pending &&
        pendingAction(pending, product.code) === "check_previous"
      ) {
        // L'opération en attente concernait un autre produit : résoudre
        // son issue avant de l'écraser. Confirmée → le paiement avait
        // abouti, on conclut sur cette commande ; sinon la purge TTL
        // restituera sa jauge et la nouvelle opération démarre libre.
        let httpOk = false;
        let prevStatus: string | null = null;
        try {
          const chk = await fetch(
            `${api}/reservations/${pending.reservationId}`,
            { cache: "no-store" }
          );
          httpOk = chk.ok;
          if (chk.ok)
            prevStatus = (await chk.json()).status ?? null;
        } catch {
          // Injoignable → état inconnu, traité ci-dessous.
        }
        const outcome = previousOutcome(httpOk, prevStatus);
        if (outcome === "conclude") {
          pendingRef.current = null;
          setPendingUI(null);
          clearPendingPayment(sessionStorage);
          setOpen(false);
          router.push(`/succes?id=${pending.reservationId}`);
          return;
        }
        if (outcome === "unknown") {
          // L'échec de lecture ne prouve rien sur le sort du paiement :
          // la clé est conservée et tout nouvel achat est bloqué
          // jusqu'à ce que l'état de l'opération soit établi.
          pendingRef.current = pending;
          toast.error(
            "Impossible de vérifier le paiement en cours — aucune nouvelle commande n'a été créée. Réessayez."
          );
          return;
        }
        pending = null;
        pendingRef.current = null;
        setPendingUI(null);
        clearPendingPayment(sessionStorage);
      }

      // `resuming` (défini en tête de fonction) : même réservation,
      // même clé — jamais de second panier.
      let reservationId = pending?.reservationId ?? "";
      let reservationTotal = pending?.amount ?? "0";

      if (!resuming) {
        const base = {
        visit_date: hasMuseumDay ? visitDate : undefined,
        session_ids: needsSessions ? selectedSessions : undefined,
      };
      const items: Record<string, unknown>[] = [];

      if (isFamily) {
        items.push({
          product_code: product.code,
          extra_children: extraChildren,
          ...base,
        });
      } else {
        for (const cat of ["adult", "child", "reduced"] as const) {
          for (let i = 0; i < counts[cat]; i++) {
            items.push({ product_code: product.code, category: cat, ...base });
          }
        }
        for (let i = 0; i < counts.under_4; i++) {
          items.push({
            product_code: product.code,
            free_profile: "under_4" satisfies FreeProfile,
            ...base,
          });
        }
        for (let i = 0; i < counts.disability; i++) {
          items.push({
            product_code: product.code,
            category: "adult",
            free_profile: "disability" satisfies FreeProfile,
            ...base,
          });
        }
        for (let i = 0; i < counts.pmr_companion; i++) {
          items.push({
            product_code: product.code,
            free_profile: "pmr_companion" satisfies FreeProfile,
            ...base,
          });
        }
      }

      // Séance supplémentaire à tarif réduit : mêmes catégories/profils
      // que les personnes de la commande → droits fusionnés sur les
      // mêmes QR (même réservation, session différente).
      if (extraSessionId && extraProduct) {
        const addExtra = (entry: Record<string, unknown>) =>
          items.push({
            product_code: extraProduct.code,
            session_ids: [extraSessionId],
            ...entry,
          });
        if (isFamily) {
          for (let i = 0; i < 2; i++) addExtra({ category: "adult" });
          for (let i = 0; i < 2 + extraChildren; i++)
            addExtra({ category: "child" });
        } else {
          for (const cat of ["adult", "child", "reduced"] as const) {
            for (let i = 0; i < counts[cat]; i++)
              addExtra({ category: cat });
          }
          for (let i = 0; i < counts.under_4; i++)
            addExtra({ free_profile: "under_4" satisfies FreeProfile });
          for (let i = 0; i < counts.disability; i++)
            addExtra({
              category: "adult",
              free_profile: "disability" satisfies FreeProfile,
            });
          for (let i = 0; i < counts.pmr_companion; i++)
            addExtra({
              free_profile: "pmr_companion" satisfies FreeProfile,
            });
        }
      }

      const res = await fetch(`${api}/reservations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          customer_email: email,
          channel: "web",
          items,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        toast.error(body.detail ?? `Erreur ${res.status} lors de la réservation.`);
        setOpen(false);
        router.refresh();
        return;
      }
      const reservation = await res.json();
      reservationId = reservation.id;
      reservationTotal = reservation.total_price;
      // L'opération est enregistrée AVANT l'appel de paiement : si la
      // réponse se perd, le prochain essai la reprendra telle quelle.
      if (Number(reservation.total_price) > 0) {
        pending = {
          reservationId: reservation.id,
          productCode: product.code,
          paymentKey: crypto.randomUUID(),
          amount: reservation.total_price,
        };
        pendingRef.current = pending;
        setPendingUI(pending);
        savePendingPayment(pending, sessionStorage);
      }
      }

      // Canal web (DFC n°7) : paiement unique par CB, simulé ici.
      // Les commandes à 0 € sont déjà confirmées — bypass intact.
      if (pending && Number(reservationTotal) > 0) {
        if (!pending.paymentKey) {
          // La tentative précédente a été rejetée (4xx certain) : la
          // clé est libérée, cette nouvelle tentative en reçoit une
          // neuve — la réservation pending n'est pas recréée.
          pending.paymentKey = crypto.randomUUID();
          pendingRef.current = pending;
          savePendingPayment(pending, sessionStorage);
        }
        const payRes = await fetch(
          `${api}/reservations/${pending.reservationId}/payments`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              method: "cb",
              amount: pending.amount,
              idempotency_key: pending.paymentKey,
            }),
          }
        );
        if (!payRes.ok) {
          const body = await payRes.json().catch(() => ({}));
          if (payRes.status >= 500) {
            // Issue incertaine : clé et paramètres conservés pour
            // reprise à la prochaine tentative.
            toast.error(
              "Erreur serveur — le résultat du paiement est incertain ; il sera repris au prochain essai."
            );
          } else {
            // Rejet métier certain (4xx) : rien n'est enregistré — la
            // clé est libérée ; si la réservation n'est plus payable
            // (expirée/annulée), l'opération est abandonnée.
            let payable = true;
            try {
              const chk = await fetch(
                `${api}/reservations/${pending.reservationId}`,
                { cache: "no-store" }
              );
              payable = chk.ok && (await chk.json()).status === "pending";
            } catch {
              payable = true; // statut inconnu → on conserve l'opération
            }
            if (payable) {
              pending.paymentKey = null;
              pendingRef.current = pending;
              setPendingUI(pending);
              savePendingPayment(pending, sessionStorage);
            } else {
              pendingRef.current = null;
              setPendingUI(null);
              clearPendingPayment(sessionStorage);
            }
            toast.error(body.detail ?? "Le paiement par carte a échoué.");
          }
          setOpen(false);
          router.refresh();
          return;
        }
      }

      pendingRef.current = null;
      setPendingUI(null);
      clearPendingPayment(sessionStorage);
      toast.success(
        estimate === 0
          ? "Entrées gratuites validées !"
          : "Paiement accepté — réservation confirmée !"
      );
      setOpen(false);
      router.push(`/succes?id=${reservationId}`);
    } catch {
      // Réseau/timeout : l'opération en attente (le cas échéant) reste
      // persistée et sera reprise au prochain essai.
      toast.error("Impossible de joindre le serveur. Réessayez plus tard.");
    } finally {
      submitInFlight.current = false;
      setSubmitting(false);
    }
  }

  const counterRow = (
    key: keyof Counts,
    label: string,
    price?: number | null
  ) => (
    <div key={key} className="flex items-center justify-between">
      <span className="text-sm">
        {label}
        <span className="ml-1.5 text-xs text-muted-foreground">
          {price != null ? eurFormatter.format(price) : "gratuit"}
        </span>
      </span>
      <div className="flex items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="icon"
          className="size-7"
          disabled={counts[key] === 0}
          onClick={() => updateCount(key, -1)}
        >
          <Minus className="size-3.5" />
        </Button>
        <span className="w-6 text-center tabular-nums">{counts[key]}</span>
        <Button
          type="button"
          variant="outline"
          size="icon"
          className="size-7"
          disabled={persons >= capacity}
          onClick={() => updateCount(key, 1)}
        >
          <Plus className="size-3.5" />
        </Button>
      </div>
    </div>
  );

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" disabled={soldOut}>
          {soldOut ? "Indisponible" : "Sélectionner"}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{product.label}</DialogTitle>
          <DialogDescription>
            {new Date(`${visitDate}T12:00:00`).toLocaleDateString("fr-FR", {
              weekday: "long",
              day: "numeric",
              month: "long",
            })}
            {highSeason && " — haute saison"}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-5">
          {pendingUI && (
            <div className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm dark:border-amber-700 dark:bg-amber-950/40">
              <p className="font-medium">
                Paiement en attente :{" "}
                {eurFormatter.format(Number(pendingUI.amount))} par CB
              </p>
              <p className="text-xs text-muted-foreground">
                Le résultat de l&apos;opération précédente est incertain —
                la commande est figée, reprenez le paiement ci-dessous.
              </p>
            </div>
          )}
          {/* Tant qu'une opération est incertaine, ses paramètres sont
              figés : le fieldset désactive tous les contrôles du panier. */}
          <fieldset
            disabled={pendingUI !== null}
            className="m-0 min-w-0 space-y-5 border-0 p-0 disabled:opacity-70"
          >
          {needsSessions && (
            <div className="space-y-2">
              <Label>
                {sessionCount > 1
                  ? `Choisissez ${sessionCount} séances`
                  : "Choisissez votre séance"}
              </Label>
              <div className="grid grid-cols-2 gap-2">
                {sessions.map((s) => {
                  const selected = selectedSessions.includes(s.id);
                  const full = !selected && selectedSessions.length >= sessionCount;
                  return (
                    <button
                      key={s.id}
                      type="button"
                      disabled={s.remaining === 0 || full}
                      onClick={() => toggleSession(s.id)}
                      className={cn(
                        "flex flex-col items-center gap-0.5 rounded-lg border px-3 py-2 text-sm transition-colors",
                        selected
                          ? "border-primary bg-primary/10 font-medium"
                          : "hover:bg-muted",
                        (s.remaining === 0 || full) &&
                          "cursor-not-allowed opacity-40"
                      )}
                    >
                      <span className="flex items-center gap-1.5">
                        <CalendarClock className="size-3.5" />
                        {s.label}
                      </span>
                      <span className="text-center text-xs font-medium leading-tight">
                        {s.show}
                      </span>
                      <span className="flex items-center gap-1 text-xs text-muted-foreground">
                        <Ticket className="size-3" />
                        {s.remaining} place{s.remaining > 1 ? "s" : ""}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {extraProduct &&
            sessions.some((s) => !selectedSessions.includes(s.id)) && (
              <div className="space-y-2 rounded-lg border border-dashed px-3 py-2.5">
                <label className="flex cursor-pointer items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={extraSessionId !== null}
                    onChange={(e) =>
                      setExtraSessionId(e.target.checked ? "" : null)
                    }
                  />
                  Séance supplémentaire —{" "}
                  {eurFormatter.format(
                    Number(extraProduct.price_adult)
                  )}
                  /pers. (tarif réduit)
                </label>
                {extraSessionId !== null && (
                  <select
                    value={extraSessionId}
                    onChange={(e) =>
                      setExtraSessionId(e.target.value || "")
                    }
                    className="h-8 w-full rounded-md border bg-background px-2 text-xs"
                  >
                    <option value="">— Choisir la séance —</option>
                    {sessions
                      .filter((s) => !selectedSessions.includes(s.id))
                      .map((s) => (
                        <option
                          key={s.id}
                          value={s.id}
                          disabled={s.remaining < persons}
                        >
                          {s.label} — {s.show} ({s.remaining} place
                          {s.remaining > 1 ? "s" : ""})
                        </option>
                      ))}
                  </select>
                )}
              </div>
            )}

          <div className="space-y-2">
            <Label htmlFor="email">Votre email</Label>
            <Input
              id="email"
              type="email"
              required
              placeholder="jack@blackpearl.fr"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>

          {isFamily ? (
            <div className="space-y-2">
              <Label>Forfait 2 adultes + 2 enfants</Label>
              <div className="flex items-center justify-between">
                <span className="text-sm">
                  Enfants supplémentaires
                  <span className="ml-1.5 text-xs text-muted-foreground">
                    {eurFormatter.format(Number(product.extra_child_price))}
                    /enfant
                  </span>
                </span>
                <div className="flex items-center gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    className="size-7"
                    disabled={extraChildren === 0}
                    onClick={() => setExtraChildren((v) => Math.max(0, v - 1))}
                  >
                    <Minus className="size-3.5" />
                  </Button>
                  <span className="w-6 text-center tabular-nums">
                    {extraChildren}
                  </span>
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    className="size-7"
                    disabled={persons >= capacity}
                    onClick={() => setExtraChildren((v) => v + 1)}
                  >
                    <Plus className="size-3.5" />
                  </Button>
                </div>
              </div>
            </div>
          ) : (
            <>
              <div className="space-y-2">
                <Label>Billets</Label>
                {PAID_ROWS.map(({ key, label, priceKey }) =>
                  counterRow(key, label, Number(product[priceKey]))
                )}
              </div>
              <div className="space-y-2">
                <Label className="text-muted-foreground">
                  Billets gratuits (justificatif requis à l&apos;entrée)
                </Label>
                {FREE_ROWS.map(({ key, label }) =>
                  counterRow(key as keyof Counts, label)
                )}
              </div>
            </>
          )}

          <div className="flex items-center justify-between rounded-lg border bg-muted/40 px-4 py-3">
            <span className="text-sm font-medium">
              {pendingUI
                ? "Montant de l'opération en attente"
                : estimate === 0
                  ? "Gratuit — aucun paiement requis"
                  : "Total estimé"}
            </span>
            <span className="text-lg font-bold tabular-nums">
              {eurFormatter.format(
                pendingUI ? Number(pendingUI.amount) : estimate
              )}
            </span>
          </div>

          <p className="text-xs text-muted-foreground">
            * Tarif réduit et gratuités : justificatif exigé au contrôle
            d&apos;accès.
          </p>
          </fieldset>

          <DialogFooter>
            <Button
              type="submit"
              disabled={
                submitting ||
                (!pendingUI &&
                  (persons === 0 ||
                    extraSessionId === "" ||
                    (needsSessions &&
                      selectedSessions.length !== sessionCount)))
              }
            >
              {submitting && <Loader2 className="size-4 animate-spin" />}
              {pendingUI
                ? `Reprendre le paiement de ${eurFormatter.format(Number(pendingUI.amount))}`
                : estimate === 0
                  ? `Valider — ${persons} entrée${persons > 1 ? "s" : ""} gratuite${persons > 1 ? "s" : ""}`
                  : `Payer ${eurFormatter.format(estimate)} par CB`}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
