"use client";

import { useEffect, useRef, useState } from "react";
import { Scanner, type IDetectedBarcode } from "@yudiel/react-qr-scanner";
import {
  CircleCheck,
  IdCard,
  OctagonX,
  ScanLine,
  Ticket,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

type ScanResult =
  | {
      status: "success";
      accessLabel: string;
      controlWarning: string | null;
      category: string;
      sessionStart: string | null;
      validDate: string | null;
      remaining: string[];
    }
  | { status: "error"; message: string };

type Checkpoint = {
  kind: "event" | "session";
  id: string;
  label: string;
};

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const CATEGORY_LABELS: Record<string, string> = {
  adult: "Adulte",
  child: "Enfant",
  reduced: "Tarif réduit",
  group: "Groupe",
  school: "Scolaire",
};

const ACCESS_LABELS: Record<string, string> = {
  open_ticket: "Musée",
  session_standard: "Théâtre",
  session_dining: "Dîner-spectacle",
};

const timeFormatter = new Intl.DateTimeFormat("fr-FR", {
  timeStyle: "short",
  timeZone: "Europe/Paris",
});

const dateFormatter = new Intl.DateTimeFormat("fr-FR", {
  dateStyle: "full",
  timeZone: "Europe/Paris",
});

const dayFormatter = new Intl.DateTimeFormat("fr-FR", {
  dateStyle: "short",
  timeZone: "Europe/Paris",
});

export default function ScannerPage() {
  const [result, setResult] = useState<ScanResult | null>(null);
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
  const [checkpointKey, setCheckpointKey] = useState<string>("");
  const processing = useRef(false);

  const checkpoint =
    checkpoints.find((c) => `${c.kind}:${c.id}` === checkpointKey) ?? null;

  useEffect(() => {
    const today = dayFormatter.format(new Date());
    fetch(`${process.env.NEXT_PUBLIC_API_URL}/events`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : []))
      .then(
        (
          events: {
            id: string;
            title: string;
            event_type: string;
            sessions?: { id: string; start_time: string }[];
          }[]
        ) => {
          const cps: Checkpoint[] = [];
          for (const ev of events) {
            if (ev.event_type === "permanent_exhibition") {
              cps.push({ kind: "event", id: ev.id, label: ev.title });
            }
            for (const s of ev.sessions ?? []) {
              if (dayFormatter.format(new Date(s.start_time)) === today) {
                cps.push({
                  kind: "session",
                  id: s.id,
                  label: `${ev.title} — ${timeFormatter.format(
                    new Date(s.start_time)
                  )}`,
                });
              }
            }
          }
          setCheckpoints(cps);
          if (cps.length > 0) {
            setCheckpointKey(`${cps[0].kind}:${cps[0].id}`);
          }
        }
      )
      .catch(() => setCheckpoints([]));
  }, []);

  function accessLabel(a: {
    access_type: string;
    session_start: string | null;
    session_event_title?: string | null;
    valid_date: string | null;
  }): string {
    const base = ACCESS_LABELS[a.access_type] ?? a.access_type;
    if (a.session_start) {
      const show = a.session_event_title
        ? ` — ${a.session_event_title}`
        : "";
      return `${base}${show} — ${timeFormatter.format(
        new Date(a.session_start)
      )}`;
    }
    if (a.valid_date) {
      return `${base} — ${dayFormatter.format(new Date(`${a.valid_date}T12:00:00`))}`;
    }
    return base;
  }

  async function handleScan(detected: IDetectedBarcode[]) {
    if (processing.current || detected.length === 0 || !checkpoint) return;
    processing.current = true;

    const rawValue = detected[0].rawValue;
    if (!UUID_RE.test(rawValue)) {
      setResult({
        status: "error",
        message: "QR code invalide — ce n'est pas un billet du musée.",
      });
      return;
    }

    try {
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL}/tickets/${rawValue}/scan`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(
            checkpoint.kind === "event"
              ? { event_id: checkpoint.id }
              : { session_id: checkpoint.id }
          ),
        }
      );
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        setResult({
          status: "success",
          accessLabel: body.access_label ?? "Billet valide",
          controlWarning: body.control_warning ?? null,
          category: CATEGORY_LABELS[body.ticket_category] ?? body.ticket_category,
          sessionStart: body.session_start ?? null,
          validDate: body.valid_date ?? null,
          remaining: (body.accesses ?? [])
            .filter((a: { is_scanned: boolean }) => !a.is_scanned)
            .map(accessLabel),
        });
      } else {
        setResult({
          status: "error",
          message:
            typeof body.detail === "string"
              ? body.detail
              : `Erreur ${res.status} lors du contrôle.`,
        });
      }
    } catch {
      setResult({
        status: "error",
        message: "Impossible de joindre le serveur.",
      });
    }
  }

  function scanNext() {
    setResult(null);
    processing.current = false;
  }

  return (
    <main className="mx-auto w-full max-w-xl flex-1 px-4 py-10">
      <Card>
        <CardHeader className="items-center text-center">
          <ScanLine className="mb-2 size-10 text-primary" aria-hidden />
          <CardTitle className="text-2xl">Contrôle d&apos;accès</CardTitle>
          <CardDescription>
            Présentez le QR code du billet devant la caméra.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <label
              htmlFor="checkpoint"
              className="text-sm font-medium leading-none"
            >
              Poste de contrôle
            </label>
            <select
              id="checkpoint"
              value={checkpointKey}
              onChange={(e) => {
                setCheckpointKey(e.target.value);
                setResult(null);
                processing.current = false;
              }}
              className="h-9 w-full rounded-md border bg-background px-2 text-sm"
            >
              {checkpoints.length === 0 && (
                <option value="">— Aucun poste disponible —</option>
              )}
              {checkpoints.map((c) => (
                <option key={`${c.kind}:${c.id}`} value={`${c.kind}:${c.id}`}>
                  {c.label}
                </option>
              ))}
            </select>
          </div>
          {result ? (
            result.status === "success" ? (
              <div
                role="alert"
                className="flex flex-col items-center gap-4 rounded-lg border-2 border-green-600 bg-green-600/10 p-8 text-center text-green-700 dark:text-green-400"
              >
                <CircleCheck className="size-16" aria-hidden />
                <p className="text-xl font-bold">{result.accessLabel}</p>
                <div className="space-y-1 text-sm">
                  <p className="flex items-center justify-center gap-1.5">
                    <Ticket className="size-4" aria-hidden />
                    {result.category}
                  </p>
                  {result.sessionStart && (
                    <p className="text-green-800 dark:text-green-300">
                      Séance :{" "}
                      {timeFormatter.format(new Date(result.sessionStart))}
                    </p>
                  )}
                  {!result.sessionStart && result.validDate && (
                    <p className="text-green-800 dark:text-green-300">
                      Valable le{" "}
                      {dateFormatter.format(
                        new Date(`${result.validDate}T12:00:00`)
                      )}
                    </p>
                  )}
                  {result.remaining.length > 0 && (
                    <p className="text-green-800 dark:text-green-300">
                      Autres droits sur ce billet :{" "}
                      {result.remaining.join(" · ")}
                    </p>
                  )}
                </div>
                {result.controlWarning && (
                  <div className="flex w-full items-center justify-center gap-2 rounded-md border-2 border-amber-500 bg-amber-500/15 px-4 py-3 text-base font-bold text-amber-700 dark:text-amber-400">
                    <IdCard className="size-5 shrink-0" aria-hidden />
                    {result.controlWarning}
                  </div>
                )}
                <Button onClick={scanNext} variant="outline">
                  Scanner le suivant
                </Button>
              </div>
            ) : (
              <div
                role="alert"
                className="flex flex-col items-center gap-4 rounded-lg border-2 border-red-600 bg-red-600/10 p-8 text-center text-red-700 dark:text-red-400"
              >
                <OctagonX className="size-16" aria-hidden />
                <p className="text-xl font-bold">Entrée refusée</p>
                <p className="text-sm">{result.message}</p>
                <Button onClick={scanNext} variant="outline">
                  Scanner le suivant
                </Button>
              </div>
            )
          ) : (
            <>
              <div className="overflow-hidden rounded-lg">
                <Scanner
                  onScan={handleScan}
                  onError={(error) =>
                    setCameraError(
                      error instanceof Error
                        ? error.message
                        : "Erreur caméra inconnue"
                    )
                  }
                  formats={["qr_code"]}
                  allowMultiple={false}
                />
              </div>
              {cameraError && (
                <p role="alert" className="text-center text-sm text-red-600">
                  {cameraError} — vérifiez l&apos;autorisation de la caméra.
                </p>
              )}
              <p className="text-center text-xs text-muted-foreground">
                Le flux vidéo reste local : seul l&apos;identifiant du billet est
                envoyé au serveur.
              </p>
            </>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
