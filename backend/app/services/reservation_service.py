import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.event import Event, EventType
from app.models.payment_transaction import (
    PaymentMethod,
    PaymentTransaction,
)
from app.models.product import (
    ComponentType,
    Product,
    ProductComponent,
    ProductKind,
)
from app.models.reservation import (
    Reservation,
    ReservationStatus,
    SalesChannel,
)
from app.models.reservation_item import FreeProfile, ReservationItem
from app.models.session import LATE_TOLERANCE, Session
from app.models.ticket import Ticket, TicketCategory, TicketType
from app.models.ticket_access import TicketAccess
from app.schemas.reservation import (
    PaymentCreate,
    ReservationCreate,
    ReservationItemCreate,
)
from app.services.pricing import MODIFIER_FAMILY, individual_modifier, is_high_season

MUSEUM_TZ = ZoneInfo("Europe/Paris")

FAMILY_BASE_ADULTS = 2
FAMILY_BASE_CHILDREN = 2

# Seuil officiel des groupes : minimum 8 personnes.
MIN_GROUP_SIZE = 8

# Durée de vie d'une commande en attente de règlement : au-delà, la
# purge libère les sièges réservés (dette technique assumée Chantier B).
PENDING_TTL = timedelta(minutes=15)

SESSION_COMPONENTS = {ComponentType.THEATER_SESSION, ComponentType.DINING_SESSION}
SESSION_TICKET_TYPE = {
    ComponentType.THEATER_SESSION: TicketType.SESSION_STANDARD,
    ComponentType.DINING_SESSION: TicketType.SESSION_DINING,
}
# Type d'événement attendu derrière chaque composant à séance.
SESSION_COMPONENT_EVENT_TYPE = {
    ComponentType.THEATER_SESSION: EventType.THEATER,
    ComponentType.DINING_SESSION: EventType.THEATER,
}

INDIVIDUAL_CATEGORIES = (
    TicketCategory.ADULT,
    TicketCategory.CHILD,
    TicketCategory.REDUCED,
)


def _persons(
    item: ReservationItemCreate | ReservationItem, product: Product
) -> list[TicketCategory]:
    """Personnes couvertes par l'item : catégories émises par billet.

    Accepte le payload de création comme la ligne persistée (mêmes
    attributs), car l'émission des billets est différée à la
    confirmation du paiement (DFC n°7).
    """
    if product.kind == ProductKind.FAMILY:
        if item.free_profile is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="free_profile n'est pas applicable à un forfait famille",
            )
        return [TicketCategory.ADULT] * FAMILY_BASE_ADULTS + [
            TicketCategory.CHILD
        ] * (FAMILY_BASE_CHILDREN + item.extra_children)
    if product.kind == ProductKind.GROUP:
        if item.free_profile is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="free_profile n'est pas applicable à un groupe",
            )
        # Le seuil (>= 8) est validé en amont dans create_reservation.
        return [TicketCategory.GROUP] * (item.group_size or 0)
    # Profils de gratuité (DFC n°6) : ticket à 0 €, catégorie physique conservée.
    if item.free_profile == FreeProfile.UNDER_4:
        return [TicketCategory.CHILD]
    if item.free_profile == FreeProfile.PMR_COMPANION:
        return [TicketCategory.ADULT]
    if item.free_profile == FreeProfile.DISABILITY:
        if item.category not in (TicketCategory.ADULT, TicketCategory.CHILD):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="category (adult ou child) requise pour le profil disability",
            )
        return [item.category]
    if item.category not in INDIVIDUAL_CATEGORIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Catégorie tarifaire requise pour « {product.code} » "
            "(adult, child ou reduced)",
        )
    return [item.category]


def _item_session_ids(item_in: ReservationItemCreate) -> list[uuid.UUID]:
    ids = list(item_in.session_ids)
    if item_in.session_id is not None:
        ids.append(item_in.session_id)
    return ids


def _session_components(product: Product) -> list[ProductComponent]:
    """Composants à séance, triés pour un mapping déterministe : les
    `session_ids` stockés sur l'item remplissent les slots dans cet
    ordre, identique à la création et à l'émission différée."""
    return sorted(
        (
            c
            for c in product.components
            if c.component_type in SESSION_COMPONENTS
        ),
        key=lambda c: c.component_type.value,
    )


def _session_slots(
    product: Product, session_ids: list[uuid.UUID]
) -> list[tuple[ComponentType, uuid.UUID]]:
    """Associe chaque slot de composant à séance à un session_id,
    dans l'ordre fourni."""
    slots: list[tuple[ComponentType, uuid.UUID]] = []
    queue = iter(session_ids)
    for c in _session_components(product):
        for _ in range(c.quantity):
            try:
                slots.append((c.component_type, next(queue)))
            except StopIteration:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Produit « {product.code} » modifié depuis la "
                    "commande : séances manquantes, billets non émis",
                )
    return slots


async def _emit_tickets(db: AsyncSession, reservation: Reservation) -> None:
    """Émet les billets à la confirmation (DFC n°7) — 1 billet par personne.

    Rejoue les paramètres figés sur les items (`visit_date`,
    `session_ids`). Chaque « personne » d'une ligne apporte ses accès
    (musée + séances) ; les droits des différentes lignes sont fusionnés
    sur le même billet quand elles partagent (catégorie, profil de
    gratuité) — fusion gloutonne : une personne se rattache au premier
    billet du groupe qui n'a pas encore un accès identique (même type /
    événement / séance). Limite assumée : deux achats distincts de même
    catégorie peuvent être fusionnés alors qu'ils visaient deux
    personnes différentes (les produits « pass » restent la voie
    recommandée pour grouper les droits).
    """
    if reservation.tickets:
        return
    session_ids = {
        uuid.UUID(s)
        for item in reservation.items
        for s in (item.session_ids or [])
    }
    sessions: dict[uuid.UUID, Session] = {}
    if session_ids:
        result = await db.execute(
            select(Session)
            .options(selectinload(Session.event))
            .where(Session.id.in_(session_ids))
        )
        sessions = {s.id: s for s in result.scalars()}

    # Billets émis, regroupés par (catégorie, profil de gratuité) pour
    # la fusion inter-lignes — ordre d'insertion conservé.
    groups: dict[
        tuple[TicketCategory, FreeProfile | None], list[Ticket]
    ] = {}
    for item in reservation.items:
        product = item.product
        slots = _session_slots(
            product,
            [uuid.UUID(s) for s in (item.session_ids or [])],
        )
        for person_category in _persons(item, product):
            accesses = [
                TicketAccess(
                    access_type=TicketType.OPEN_TICKET,
                    event_id=c.event_id,
                    valid_date=item.visit_date,
                )
                for c in product.components
                if c.component_type == ComponentType.MUSEUM_DAY
                for _ in range(c.quantity)
            ]
            for component_type, session_id in slots:
                session = sessions.get(session_id)
                if session is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Séance supprimée depuis la commande — "
                        "billets non émis",
                    )
                accesses.append(
                    TicketAccess(
                        access_type=SESSION_TICKET_TYPE[component_type],
                        session_id=session.id,
                        event_id=session.event_id,
                        valid_date=_session_day(session),
                    )
                )

            tickets = groups.setdefault(
                (person_category, item.free_profile), []
            )
            keys = {
                (a.access_type, a.event_id, a.session_id) for a in accesses
            }
            ticket = next(
                (
                    t
                    for t in tickets
                    if not {
                        (a.access_type, a.event_id, a.session_id)
                        for a in t.accesses
                    }
                    & keys
                ),
                None,
            )
            if ticket is None:
                ticket = Ticket(ticket_category=person_category)
                item.tickets.append(ticket)
                reservation.tickets.append(ticket)
                tickets.append(ticket)
            ticket.accesses.extend(accesses)


def _session_day(session: Session) -> date:
    return session.start_time.astimezone(MUSEUM_TZ).date()


async def create_reservation(
    db: AsyncSession, data: ReservationCreate
) -> Reservation:
    """Crée une réservation panier (produits -> items -> tickets).

    Règles clés :
    - prix recalculés côté serveur depuis le catalogue `products`
      (modificateur haute saison appliqué dynamiquement, DFC n°5) ;
    - toutes les séances visées sont verrouillées SELECT ... FOR UPDATE,
      triées par id pour éviter les deadlocks ;
    - un Pass impose visit_date == jour de chaque séance (Pass « Journée »).
    """
    codes = [item.product_code for item in data.items]
    result = await db.execute(
        select(Product)
        .options(selectinload(Product.components))
        .where(Product.code.in_(codes))
    )
    products = {p.code: p for p in result.scalars()}
    for item_in in data.items:
        product = products.get(item_in.product_code)
        if product is None or not product.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Produit inconnu ou inactif : « {item_in.product_code} »",
            )

    # --- Validation structurelle + collecte des séances à verrouiller ---
    plans: list[dict] = []
    all_session_ids: set[uuid.UUID] = set()
    for item_in in data.items:
        product = products[item_in.product_code]
        components = product.components
        has_museum_day = any(
            c.component_type == ComponentType.MUSEUM_DAY for c in components
        )
        required_sessions = sum(
            c.quantity for c in components if c.component_type in SESSION_COMPONENTS
        )
        session_ids = _item_session_ids(item_in)

        if has_museum_day and item_in.visit_date is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"visit_date requise pour « {product.code} »",
            )
        if (
            len(session_ids) != required_sessions
            or len(set(session_ids)) != len(session_ids)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"« {product.code} » attend {required_sessions} séance(s) "
                f"distincte(s), {len(set(session_ids))} distincte(s) "
                f"fournie(s) sur {len(session_ids)}",
            )
        if product.kind != ProductKind.FAMILY and item_in.extra_children:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"extra_children n'a de sens que pour un forfait famille",
            )
        if product.kind == ProductKind.GROUP:
            if (
                item_in.group_size is None
                or item_in.group_size < MIN_GROUP_SIZE
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"« {product.code} » est un produit groupe : "
                    f"minimum {MIN_GROUP_SIZE} personnes",
                )
        elif item_in.group_size is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="group_size n'a de sens que pour un produit groupe",
            )

        # Les session_ids fournis remplissent les slots des composants à
        # séance, dans l'ordre (même mapping pour toutes les personnes).
        session_slots = _session_slots(product, session_ids)

        all_session_ids.update(session_ids)
        plans.append(
            {
                "item_in": item_in,
                "product": product,
                "persons": _persons(item_in, product),
                "session_ids": session_ids,
                "session_slots": session_slots,
                "has_museum_day": has_museum_day,
            }
        )

    # Produits « add-on » (is_addon) : droits complémentaires à tarif
    # réduit — chaque personne add-on doit être couverte par un produit
    # de base accordant le même droit dans la même commande. Sinon
    # « musée + séance supp. » (17 €) court-circuiterait le Pass 1
    # Spectacle (20 €) et `extra_show` seul le billet théâtre plein
    # tarif.
    addon_needs: Counter = Counter()
    base_cover: Counter = Counter()
    for plan in plans:
        granted = {
            c.component_type for c in plan["product"].components
        }
        target = addon_needs if plan["product"].is_addon else base_cover
        for component_type in granted:
            target[component_type] += len(plan["persons"])
    if any(
        needed > base_cover.get(component_type, 0)
        for component_type, needed in addon_needs.items()
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La séance supplémentaire est un complément : elle "
            "exige un billet ou un pass avec séance pour chaque "
            "personne dans la même commande",
        )

    # Accompagnateur PMR : max 1 par porteur de carte d'invalidité (DFC n°6).
    profiles = [
        plan["item_in"].free_profile for plan in plans
    ]
    if profiles.count(FreeProfile.PMR_COMPANION) > profiles.count(
        FreeProfile.DISABILITY
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Un accompagnateur PMR exige un porteur de carte "
            "d'invalidité dans la même commande",
        )

    # --- Verrouillage anti-surbooking, trié par id (anti-deadlock) ---
    sessions: dict[uuid.UUID, Session] = {}
    if all_session_ids:
        result = await db.execute(
            select(Session)
            .options(selectinload(Session.event))
            .where(Session.id.in_(sorted(all_session_ids)))
            .order_by(Session.id)
            .with_for_update()
        )
        sessions = {s.id: s for s in result.scalars()}
        missing = all_session_ids - sessions.keys()
        if missing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Séance introuvable",
            )
        now = datetime.now(timezone.utc)
        for session in sessions.values():
            if not session.event.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Événement inactif, réservation impossible",
                )
            # Même fenêtre que le contrôle d'accès : une séance dont le
            # début + tolérance est dépassé ne peut plus être vendue,
            # quel que soit le canal (web, caisse…).
            if now > session.start_time + LATE_TOLERANCE:
                start_paris = session.start_time.astimezone(MUSEUM_TZ)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Séance du {start_paris.strftime('%d/%m/%Y')} "
                    f"à {start_paris.strftime('%Hh%M')} déjà passée — "
                    "vente impossible",
                )

    # Événements liés aux accès Musée (open_ticket) — contrôle is_active.
    museum_event_ids = {
        c.event_id
        for plan in plans
        for c in plan["product"].components
        if c.component_type == ComponentType.MUSEUM_DAY and c.event_id
    }
    museum_events: dict[uuid.UUID, Event] = {}
    if museum_event_ids:
        result = await db.execute(
            select(Event).where(Event.id.in_(museum_event_ids))
        )
        museum_events = {e.id: e for e in result.scalars()}

    # --- Cohérence métier + calcul des jaugeages ---
    seats_needed: Counter = Counter()
    for plan in plans:
        item_in = plan["item_in"]
        product = plan["product"]
        visit_date = item_in.visit_date

        expected_types = {
            SESSION_COMPONENT_EVENT_TYPE[c.component_type]
            for c in product.components
            if c.component_type in SESSION_COMPONENTS
        }
        for session_id in plan["session_ids"]:
            session = sessions[session_id]
            if session.event.event_type not in expected_types:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"La séance {session_id} n'est pas une séance de théâtre",
                )
            if visit_date is not None and _session_day(session) != visit_date:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Pass « Journée » : la séance du "
                    f"{_session_day(session)} ne coïncide pas avec "
                    f"visit_date {visit_date}",
                )
        for _, session_id in plan["session_slots"]:
            seats_needed[session_id] += len(plan["persons"])

        for c in product.components:
            if c.component_type == ComponentType.MUSEUM_DAY:
                if c.event_id is None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Produit « {product.code} » mal configuré : "
                        "accès musée sans événement associé",
                    )
                event = museum_events.get(c.event_id)
                if event is None or not event.is_active:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Événement musée inactif ou introuvable",
                    )

    for session_id, needed in seats_needed.items():
        session = sessions[session_id]
        remaining = session.max_capacity - session.booked_seats
        if needed > remaining:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Capacité insuffisante : {remaining} place(s) restante(s)",
            )

    # --- Calcul des prix + figeage des paramètres d'émission ---
    # DFC n°7 : les billets ne sont émis qu'une fois le solde à 0.
    price_by_category = {
        TicketCategory.ADULT: "price_adult",
        TicketCategory.CHILD: "price_child",
        TicketCategory.REDUCED: "price_reduced",
    }
    reservation = Reservation(
        customer_email=data.customer_email,
        total_price=Decimal(0),
        sales_channel=data.channel,
    )
    total = Decimal(0)

    for plan in plans:
        item_in = plan["item_in"]
        product = plan["product"]
        if item_in.visit_date is not None:
            ref_date = item_in.visit_date
        elif plan["session_ids"]:
            ref_date = _session_day(sessions[plan["session_ids"][0]])
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Produit « {product.code} » mal configuré : "
                "ni journée ni séance à dater",
            )
        high_season = await is_high_season(db, ref_date)

        if item_in.free_profile is not None:
            # DFC n°6 : le billet existe toujours, le prix est nul.
            base = Decimal(0)
            modifier = Decimal(0)
            item_category = item_in.category
        elif product.kind == ProductKind.GROUP:
            # Tarif groupe : `price_adult` du produit = prix par personne.
            if product.price_adult is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Produit « {product.code} » mal configuré : "
                    "prix par personne manquant",
                )
            base = product.price_adult * item_in.group_size
            modifier = individual_modifier(
                TicketCategory.ADULT, high_season
            ) * item_in.group_size
            item_category = TicketCategory.GROUP
        elif product.kind == ProductKind.FAMILY:
            base = product.family_base_price + product.extra_child_price * item_in.extra_children
            modifier = MODIFIER_FAMILY if high_season else Decimal(0)
            item_category = None
        else:
            base = getattr(product, price_by_category[item_in.category])
            modifier = individual_modifier(item_in.category, high_season)
            item_category = item_in.category

        item = ReservationItem(
            product=product,
            category=item_category,
            extra_children=item_in.extra_children,
            free_profile=item_in.free_profile,
            visit_date=item_in.visit_date,
            session_ids=[str(s) for s in dict.fromkeys(plan["session_ids"])],
            group_size=item_in.group_size,
            computed_price=base + modifier,
            season_modifier=modifier,
        )
        total += item.computed_price
        reservation.items.append(item)

    # Le compteur de jauge n'est incrémenté qu'après validation complète.
    for session_id, needed in seats_needed.items():
        sessions[session_id].booked_seats += needed

    reservation.total_price = total
    if total == 0:
        # DFC n°6 : panier à 0 € — confirmation immédiate, billets émis
        # sans passer par un encaissement.
        reservation.status = ReservationStatus.CONFIRMED
        await _emit_tickets(db, reservation)
    db.add(reservation)
    await db.commit()

    result = await db.execute(
        select(Reservation)
        .options(
            selectinload(Reservation.items)
            .selectinload(ReservationItem.tickets)
            .selectinload(Ticket.accesses)
            .selectinload(TicketAccess.session)
            .selectinload(Session.event),
            selectinload(Reservation.items).selectinload(ReservationItem.product),
            selectinload(Reservation.tickets)
            .selectinload(Ticket.accesses)
            .selectinload(TicketAccess.session)
            .selectinload(Session.event),
            selectinload(Reservation.payments),
        )
        .where(Reservation.id == reservation.id)
    )
    return result.scalar_one()


def _has_group_or_school(reservation: Reservation) -> bool:
    """La commande contient-elle une vente groupe/scolaire ?
    Condition d'éligibilité au paiement par chèque (DFC n°7)."""
    return any(
        item.category in (TicketCategory.GROUP, TicketCategory.SCHOOL)
        or item.product.kind == ProductKind.GROUP
        for item in reservation.items
    )


async def add_payment(
    db: AsyncSession, reservation_id: uuid.UUID, data: PaymentCreate
) -> tuple[PaymentTransaction, Decimal, Decimal, Reservation]:
    """Encaisse une tranche de paiement (DFC n°7).

    Retourne (transaction, rendu de monnaie, reste à payer, réservation).
    La réservation passe à `confirmed` et ses billets sont émis dès que
    le solde atteint 0.
    """
    result = await db.execute(
        select(Reservation)
        .options(
            selectinload(Reservation.items)
            .selectinload(ReservationItem.product)
            .selectinload(Product.components),
            selectinload(Reservation.items)
            .selectinload(ReservationItem.tickets)
            .selectinload(Ticket.accesses)
            .selectinload(TicketAccess.session)
            .selectinload(Session.event),
            selectinload(Reservation.tickets)
            .selectinload(Ticket.accesses)
            .selectinload(TicketAccess.session)
            .selectinload(Session.event),
            selectinload(Reservation.payments),
        )
        .where(Reservation.id == reservation_id)
        .with_for_update()
    )
    reservation = result.scalar_one_or_none()
    if reservation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Réservation introuvable",
        )
    if reservation.status != ReservationStatus.PENDING:
        labels = {
            ReservationStatus.CONFIRMED: "Réservation déjà soldée",
            ReservationStatus.CANCELLED: "Réservation annulée",
            ReservationStatus.EXPIRED: "Réservation expirée (délai de "
            "paiement dépassé)",
        }
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=labels.get(reservation.status, "Réservation inactive"),
        )

    remaining = reservation.amount_due

    # --- Règles par canal de vente (DFC n°7 §1) ---
    if reservation.sales_channel == SalesChannel.WEB:
        if data.method != PaymentMethod.CB:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La billetterie en ligne n'accepte que la carte "
                "bancaire",
            )
        if data.amount != remaining:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Le canal web exige un paiement CB unique du "
                f"montant total ({remaining} €)",
            )
    elif data.method == PaymentMethod.CHECK and not _has_group_or_school(
        reservation
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le paiement par chèque est réservé aux groupes et "
            "scolaires",
        )

    # --- Imputation de la tranche (DFC n°7 §3) ---
    if data.method in (PaymentMethod.CB, PaymentMethod.CHECK):
        if data.amount > remaining:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Montant supérieur au reste à payer ({remaining} €)",
            )
        applied = data.amount
        change_due = Decimal(0)
    else:
        # Espèces et ANCV : le nominal remis peut excéder le solde.
        applied = min(data.amount, remaining)
        # Rendu de monnaie uniquement pour les espèces — jamais d'ANCV.
        change_due = (
            data.amount - applied
            if data.method == PaymentMethod.CASH
            else Decimal(0)
        )

    payment = PaymentTransaction(
        reservation_id=reservation.id,
        method=data.method,
        amount=data.amount,
        applied_amount=applied,
    )
    reservation.payments.append(payment)

    new_due = remaining - applied
    if new_due == 0:
        reservation.status = ReservationStatus.CONFIRMED
        await _emit_tickets(db, reservation)

    db.add(payment)
    await db.commit()
    return payment, change_due, new_due, reservation


async def cancel_reservation(
    db: AsyncSession, reservation_id: uuid.UUID
) -> Reservation:
    """Annule une commande `pending` et restitue les places réservées.

    Cas d'usage caisse : l'agent déverrouille un panier validé si le
    client change d'avis, puis re-soumet une nouvelle commande. Les
    encaissements éventuels restent tracés en base (audit) — le
    remboursement se fait manuellement.
    """
    result = await db.execute(
        select(Reservation)
        .options(
            selectinload(Reservation.items).selectinload(
                ReservationItem.product
            ),
            selectinload(Reservation.payments),
        )
        .where(Reservation.id == reservation_id)
        .with_for_update()
    )
    reservation = result.scalar_one_or_none()
    if reservation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Réservation introuvable",
        )
    if reservation.status != ReservationStatus.PENDING:
        labels = {
            ReservationStatus.CONFIRMED: "Réservation déjà soldée — "
            "annulation impossible (remboursement requis)",
            ReservationStatus.CANCELLED: "Réservation déjà annulée",
            ReservationStatus.EXPIRED: "Réservation déjà expirée",
        }
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=labels.get(reservation.status, "Réservation inactive"),
        )

    session_ids = {
        uuid.UUID(s)
        for item in reservation.items
        for s in (item.session_ids or [])
    }
    sessions: dict[uuid.UUID, Session] = {}
    if session_ids:
        result = await db.execute(
            select(Session)
            .where(Session.id.in_(sorted(session_ids)))
            .order_by(Session.id)
            .with_for_update()
        )
        sessions = {s.id: s for s in result.scalars()}

    for item in reservation.items:
        persons = len(_persons(item, item.product))
        for s in item.session_ids or []:
            session = sessions.get(uuid.UUID(s))
            if session is not None:
                session.booked_seats = max(0, session.booked_seats - persons)

    reservation.status = ReservationStatus.CANCELLED
    await db.commit()

    result = await db.execute(
        select(Reservation)
        .options(
            selectinload(Reservation.items)
            .selectinload(ReservationItem.tickets)
            .selectinload(Ticket.accesses)
            .selectinload(TicketAccess.session)
            .selectinload(Session.event),
            selectinload(Reservation.items).selectinload(ReservationItem.product),
            selectinload(Reservation.tickets)
            .selectinload(Ticket.accesses)
            .selectinload(TicketAccess.session)
            .selectinload(Session.event),
            selectinload(Reservation.payments),
        )
        .where(Reservation.id == reservation.id)
    )
    return result.scalar_one()


async def purge_expired_reservations(
    db: AsyncSession, ttl: timedelta = PENDING_TTL
) -> list[uuid.UUID]:
    """Expire les réservations `pending` trop vieilles et libère la jauge.

    Les sièges sont réservés dès la création de la commande (`booked_seats`)
    alors que les billets ne sont émis qu'à la confirmation : un panier
    abandonné bloquerait sinon la vente indéfiniment. La purge passe la
    réservation en `expired` et restitue les places de chaque séance
    touchée, au prorata des personnes prévues par item.
    """
    cutoff = datetime.now(timezone.utc) - ttl
    result = await db.execute(
        select(Reservation)
        .options(
            selectinload(Reservation.items).selectinload(
                ReservationItem.product
            )
        )
        .where(
            Reservation.status == ReservationStatus.PENDING,
            Reservation.created_at < cutoff,
        )
        .with_for_update()
    )
    expired = list(result.scalars().unique())
    if not expired:
        return []

    session_ids = {
        uuid.UUID(s)
        for resa in expired
        for item in resa.items
        for s in (item.session_ids or [])
    }
    sessions: dict[uuid.UUID, Session] = {}
    if session_ids:
        result = await db.execute(
            select(Session)
            .where(Session.id.in_(sorted(session_ids)))
            .order_by(Session.id)
            .with_for_update()
        )
        sessions = {s.id: s for s in result.scalars()}

    for resa in expired:
        for item in resa.items:
            persons = len(_persons(item, item.product))
            for s in item.session_ids or []:
                session = sessions.get(uuid.UUID(s))
                if session is not None:
                    session.booked_seats = max(
                        0, session.booked_seats - persons
                    )
        resa.status = ReservationStatus.EXPIRED

    await db.commit()
    return [resa.id for resa in expired]
