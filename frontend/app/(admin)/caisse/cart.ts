// Logique panier du terminal POS — fonctions pures, testées dans cart.test.ts.
// Les montants calculés ici ne sont qu'une estimation d'affichage : le serveur
// re-calcule tous les prix à la validation (moteur métier unique).

export type SessionOption = {
  id: string;
  label: string;
  show: string;
  remaining: number;
  expired: boolean;
};

export type Product = {
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

export type Tariff =
  | "adult"
  | "child"
  | "reduced"
  | "under_4"
  | "disability"
  | "pmr_companion";

// Une ligne = un produit × N personnes. Pour `simple`/`pass`, `counts`
// porte le nombre de personnes par tarif (y compris profils gratuits) ;
// `family` et `group` ont leur composition propre (2A+2E / groupSize).
// `optimized` marque une ligne créée par une optimisation du panier
// (badge « Optimisé » dans l'interface).
export type CartLine = {
  id: number;
  product: Product;
  counts: Record<Tariff, number>;
  groupSize: number;
  extraChildren: number;
  sessionIds: string[];
  optimized?: boolean;
};

export const MIN_GROUP_SIZE = 8;

export const MOD_ADULT_REDUCED = 2;
export const MOD_CHILD = 1;
export const MOD_FAMILY = 5;

export const PASS_1_CODE = "pass_1_show";

export const PAID_TARIFFS: Tariff[] = ["adult", "child", "reduced"];
export const FREE_PROFILE_TARIFFS: Tariff[] = [
  "under_4",
  "disability",
  "pmr_companion",
];
export const ALL_TARIFFS: Tariff[] = [...PAID_TARIFFS, ...FREE_PROFILE_TARIFFS];
export const FREE_TARIFFS = new Set<Tariff>(FREE_PROFILE_TARIFFS);

export function sessionCountOf(product: Product): number {
  return product.components
    .filter((c) => c.component_type !== "museum_day")
    .reduce((sum, c) => sum + c.quantity, 0);
}

export function hasMuseumDay(product: Product): boolean {
  return product.components.some((c) => c.component_type === "museum_day");
}

export function zeroCounts(): Record<Tariff, number> {
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
export function countsOf(line: CartLine): Record<Tariff, number> {
  if (line.product.kind === "family") {
    return { ...zeroCounts(), adult: 2, child: 2 + line.extraChildren };
  }
  if (line.product.kind === "group") {
    return { ...zeroCounts(), adult: line.groupSize };
  }
  return line.counts;
}

export function linePersons(line: CartLine): number {
  const c = countsOf(line);
  return ALL_TARIFFS.reduce((sum, t) => sum + c[t], 0);
}

export function lineEstimate(line: CartLine, highSeason: boolean): number {
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

export function autoSessions(
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

export function autoSessionExcluding(
  sessions: SessionOption[],
  exclude: string[]
): string {
  return (
    sessions.find(
      (s) => s.remaining > 0 && !s.expired && !exclude.includes(s.id)
    )?.id ?? ""
  );
}

export function regularLines(lines: CartLine[]): CartLine[] {
  return lines.filter((l) => !l.product.is_addon);
}

// ---------------------------------------------------------------------------
// Règle PMR (miroir du moteur métier — le serveur reste l'autorité)
// ---------------------------------------------------------------------------

// Présence d'une ligne : chaque séance choisie, plus la visite musée
// (jour commun `visitDate` — le POS n'expose pas l'événement ; le
// catalogue n'a qu'un espace musée). Même convention que le backend :
// même session_id, ou même accès musée le même jour.
export function presenceKeys(
  line: CartLine,
  visitDate: string
): Set<string> {
  const keys = new Set(line.sessionIds.filter(Boolean));
  if (hasMuseumDay(line.product)) keys.add(`museum:${visitDate}`);
  return keys;
}

// Erreur PMR d'une ligne, ou null. Règles (agrégées sur le panier, sans
// appariement nominatif) :
//  - total accompagnateurs ≤ total porteurs d'invalidité, comptés en
//    personnes — les lignes add-on portent des accès supplémentaires de
//    personnes déjà couvertes, pas des personnes nouvelles ;
//  - chaque ligne à accompagnateurs partage une séance ou la visite
//    musée avec au moins une ligne à porteurs (tous les accès des
//    porteurs comptent comme présence réelle, y compris leurs add-ons).
export function pmrLineError(
  line: CartLine,
  lines: CartLine[],
  visitDate: string
): string | null {
  if (line.product.is_addon || countsOf(line).pmr_companion === 0) {
    return null;
  }
  const regular = regularLines(lines);
  const companions = regular.reduce(
    (s, l) => s + countsOf(l).pmr_companion,
    0
  );
  const bearers = regular.reduce((s, l) => s + countsOf(l).disability, 0);
  if (companions > bearers) {
    return "1 accompagnateur PMR par personne en invalidité";
  }
  const bearerPresence = new Set(
    lines
      .filter((l) => countsOf(l).disability > 0)
      .flatMap((l) => [...presenceKeys(l, visitDate)])
  );
  if (
    [...presenceKeys(line, visitDate)].every(
      (k) => !bearerPresence.has(k)
    )
  ) {
    return "chaque accompagnateur doit partager une séance ou la visite avec un porteur";
  }
  return null;
}

export const isMuseumOnly = (l: CartLine) =>
  l.product.kind === "simple" &&
  hasMuseumDay(l.product) &&
  sessionCountOf(l.product) === 0;

export const isShowOnly = (l: CartLine) =>
  l.product.kind === "simple" &&
  !l.product.is_addon &&
  !hasMuseumDay(l.product) &&
  sessionCountOf(l.product) > 0;

// ---------------------------------------------------------------------------
// Optimisation « Pass 1 Spectacle »
// ---------------------------------------------------------------------------

export type PassHint = {
  counts: Record<Tariff, number>;
  persons: number;
  savings: number;
  sessionId: string;
};

function unitPrice(p: Product, t: Tariff): number {
  return t === "adult"
    ? Number(p.price_adult)
    : t === "child"
      ? Number(p.price_child)
      : t === "reduced"
        ? Number(p.price_reduced)
        : 0;
}

function makeHint(
  counts: Record<Tariff, number>,
  museumLines: CartLine[],
  showLines: CartLine[],
  pass: Product
): PassHint | null {
  const persons = ALL_TARIFFS.reduce((s, t) => s + counts[t], 0);
  if (persons === 0) return null;
  const museum = museumLines[0].product;
  const show = showLines[0].product;
  // Le modificateur haute saison s'annule des deux côtés : l'économie ne
  // dépend que des prix unitaires du catalogue.
  const savings = PAID_TARIFFS.reduce(
    (s, t) =>
      s +
      counts[t] *
        (unitPrice(museum, t) + unitPrice(show, t) - unitPrice(pass, t)),
    0
  );
  if (savings <= 0) return null;
  const sessionId =
    showLines.flatMap((l) => l.sessionIds).find(Boolean) ?? "";
  return { counts, persons, savings, sessionId };
}

// Suggestion explicite : conversion possible au `min` par tarif entre les
// lignes musée et théâtre (les reliquats éventuels restent facturés à
// l'unité). Utilisée pour la bannière et l'absorption au clic sur le Pass.
export function passSuggestion(
  lines: CartLine[],
  products: Product[]
): PassHint | null {
  const pass = products.find((p) => p.code === PASS_1_CODE);
  const museumLines = regularLines(lines).filter(isMuseumOnly);
  const showLines = regularLines(lines).filter(isShowOnly);
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
  return makeHint(counts, museumLines, showLines, pass);
}

// Correspondance stricte : les totaux par tarif des lignes musée et théâtre
// sont identiques — chaque personne couverte au musée l'est aussi au
// théâtre, et réciproquement. C'est le seul cas où la conversion
// automatique ne risque pas de fusionner les achats de personnes
// différentes.
export function strictPassHint(
  lines: CartLine[],
  products: Product[]
): PassHint | null {
  const pass = products.find((p) => p.code === PASS_1_CODE);
  const museumLines = regularLines(lines).filter(isMuseumOnly);
  const showLines = regularLines(lines).filter(isShowOnly);
  if (!pass || museumLines.length === 0 || showLines.length === 0) {
    return null;
  }
  const counts = zeroCounts();
  for (const t of ALL_TARIFFS) {
    const m = museumLines.reduce((s, l) => s + l.counts[t], 0);
    const s = showLines.reduce((s2, l) => s2 + l.counts[t], 0);
    if (m !== s) return null;
    counts[t] = m;
  }
  return makeHint(counts, museumLines, showLines, pass);
}

// Consomme `hint.counts` sur les lignes musée/théâtre (les lignes vidées
// sont supprimées — jamais de musée + pass facturés en double) puis ajoute
// la ligne Pass marquée « Optimisé ».
export function absorbIntoPass(
  lines: CartLine[],
  hint: PassHint,
  pass: Product,
  id: number
): CartLine[] {
  // Le budget `toMove` est propre à chaque famille de lignes (musée puis
  // théâtre) : un budget partagé viderait le premier `map` et laisserait
  // les lignes théâtre intactes — c'est le doublon « entrée + pass ».
  const consume = (pred: (l: CartLine) => boolean) => {
    const toMove = { ...hint.counts };
    return (l: CartLine) => {
      if (!pred(l)) return l;
      const c = { ...l.counts };
      for (const t of ALL_TARIFFS) {
        const take = Math.min(c[t], toMove[t]);
        c[t] -= take;
        toMove[t] -= take;
      }
      return { ...l, counts: c };
    };
  };
  const next = lines
    .map(consume(isMuseumOnly))
    .map(consume(isShowOnly))
    .filter((l) => linePersons(l) > 0);
  next.push({
    id,
    product: pass,
    counts: { ...hint.counts },
    groupSize: MIN_GROUP_SIZE,
    extraChildren: 0,
    sessionIds: [hint.sessionId],
    optimized: true,
  });
  return next;
}

// Miroir de composition à l'ajout d'une ligne : seules les lignes qui ne
// couvrent pas déjà un droit du nouveau produit sont copiées — un pass ou
// une séance déjà au panier ne fait pas monter les compteurs en double.
function sharesComponent(a: Product, b: Product): boolean {
  const types = new Set(a.components.map((c) => c.component_type));
  return b.components.some((c) => types.has(c.component_type));
}

export function copiableCounts(
  lines: CartLine[],
  product: Product
): Record<Tariff, number> {
  const counts = zeroCounts();
  for (const l of regularLines(lines)) {
    if (sharesComponent(l.product, product)) continue;
    const c = countsOf(l);
    for (const t of ALL_TARIFFS) counts[t] += c[t];
  }
  if (ALL_TARIFFS.every((t) => counts[t] === 0)) counts.adult = 1;
  return counts;
}

export type AddResult = {
  lines: CartLine[];
  nextId: number;
  // Non-null si l'ajout a déclenché une optimisation (conversion auto
  // stricte ou absorption par un pass) — sert au toast de l'interface.
  optimized: PassHint | null;
  absorbed: boolean;
};

export function addProductToCart(
  lines: CartLine[],
  product: Product,
  products: Product[],
  sessions: SessionOption[],
  nextId: number
): AddResult {
  if (product.kind === "simple" || product.kind === "pass") {
    // Ajout direct d'un pass alors que les lignes simples correspondantes
    // existent déjà : le pass les absorbe au lieu de se superposer.
    if (product.code === PASS_1_CODE) {
      const hint = passSuggestion(lines, products);
      if (hint) {
        return {
          lines: absorbIntoPass(lines, hint, product, nextId),
          nextId: nextId + 1,
          optimized: hint,
          absorbed: true,
        };
      }
    }
    // Produit déjà au panier : +1 adulte sur la ligne existante.
    const same = regularLines(lines).find(
      (l) => l.product.code === product.code
    );
    let next: CartLine[];
    let used = nextId;
    if (same) {
      next = lines.map((l) =>
        l.id === same.id
          ? { ...l, counts: { ...l.counts, adult: l.counts.adult + 1 } }
          : l
      );
    } else {
      // Nouvelle ligne : composition miroir des droits non encore couverts.
      next = [
        ...lines,
        {
          id: nextId,
          product,
          counts: copiableCounts(lines, product),
          groupSize: MIN_GROUP_SIZE,
          extraChildren: 0,
          sessionIds: autoSessions(product, sessions),
        },
      ];
      used = nextId + 1;
    }
    // Conversion automatique uniquement sur correspondance stricte ; les
    // cas partiels ou ambigus restent une suggestion explicite.
    const strict = strictPassHint(next, products);
    const pass = products.find((p) => p.code === PASS_1_CODE);
    if (strict && pass) {
      return {
        lines: absorbIntoPass(next, strict, pass, used),
        nextId: used + 1,
        optimized: strict,
        absorbed: false,
      };
    }
    return { lines: next, nextId: used, optimized: null, absorbed: false };
  }
  return {
    lines: [
      ...lines,
      {
        id: nextId,
        product,
        counts: zeroCounts(),
        groupSize: MIN_GROUP_SIZE,
        extraChildren: 0,
        sessionIds: autoSessions(product, sessions),
      },
    ],
    nextId: nextId + 1,
    optimized: null,
    absorbed: false,
  };
}
