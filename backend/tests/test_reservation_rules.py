"""Matrice de validation de create_reservation — règles de rejet métier.

Ces règles étaient jusque-là non couvertes (ni testées ni exercées) :
les E2E ne pouvaient pas les atteindre une à une sans un setup lourd.
Ici chaque cas vise la règle directement au niveau service.
"""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models import (
    ComponentType,
    EventType,
    FreeProfile,
    ProductComponent,
    ProductKind,
    SalesChannel,
    Session,
)
from app.schemas.reservation import (
    ReservationCreate,
    ReservationItemCreate,
)
from app.services.reservation_service import create_reservation
from tests.factories import (
    MUSEUM_TZ,
    add_event,
    add_product,
    add_session,
    museum_comp,
)


def item(code: str, **kw) -> ReservationItemCreate:
    return ReservationItemCreate(product_code=code, **kw)


def order(*items: ReservationItemCreate, channel=SalesChannel.WEB):
    return ReservationCreate(
        customer_email="client@pirates.fr", channel=channel, items=list(items)
    )


def day_of(session: Session) -> date:
    return session.start_time.astimezone(MUSEUM_TZ).date()


def paris_today() -> date:
    return datetime.now(MUSEUM_TZ).date()


async def test_produit_inconnu(db, catalog, today):
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db, order(item("code_inexistant", category="adult", visit_date=today))
        )
    assert e.value.status_code == 400


async def test_produit_inactif(db, catalog, today):
    catalog.museum_entry.is_active = False
    await db.flush()
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(item("museum_entry", category="adult", visit_date=today)),
        )
    assert e.value.status_code == 400


async def test_visit_date_requise_produit_musee(db, catalog):
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db, order(item("museum_entry", category="adult"))
        )
    assert e.value.status_code == 400


async def test_nombre_de_seances_exact(db, catalog):
    # pass_1_show exige exactement 1 séance — 0 fournie.
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "pass_1_show",
                    category="adult",
                    visit_date=day_of(catalog.session),
                )
            ),
        )
    assert e.value.status_code == 400


async def test_sessions_en_double_refusees(db, catalog):
    # pass_2_shows exige 2 séances DISTINCTES.
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "pass_2_shows",
                    category="adult",
                    visit_date=day_of(catalog.session),
                    session_ids=[catalog.session.id, catalog.session.id],
                )
            ),
        )
    assert e.value.status_code == 400


async def test_extra_children_hors_famille(db, catalog, today):
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "museum_entry",
                    category="adult",
                    visit_date=today,
                    extra_children=1,
                )
            ),
        )
    assert e.value.status_code == 400


async def test_groupe_sous_le_seuil(db, catalog, today):
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(item("group_visit", group_size=5, visit_date=today)),
        )
    assert e.value.status_code == 400


async def test_group_size_sur_produit_simple(db, catalog, today):
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "museum_entry",
                    category="adult",
                    group_size=10,
                    visit_date=today,
                )
            ),
        )
    assert e.value.status_code == 400


async def test_free_profile_sur_forfait_famille(db, catalog, today):
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "family_museum",
                    visit_date=today,
                    free_profile=FreeProfile.UNDER_4,
                )
            ),
        )
    assert e.value.status_code == 400


async def test_free_profile_sur_groupe(db, catalog, today):
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "group_visit",
                    group_size=8,
                    visit_date=today,
                    free_profile=FreeProfile.UNDER_4,
                )
            ),
        )
    assert e.value.status_code == 400


async def test_disability_sans_categorie(db, catalog, today):
    # Le profil disability exige category adult/child (catégorie physique).
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "museum_entry",
                    visit_date=today,
                    free_profile=FreeProfile.DISABILITY,
                )
            ),
        )
    assert e.value.status_code == 400


async def test_pmr_companion_sans_porteur(db, catalog, today):
    # Max 1 accompagnateur par porteur disability dans la commande.
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "museum_entry",
                    visit_date=today,
                    free_profile=FreeProfile.PMR_COMPANION,
                )
            ),
        )
    assert e.value.status_code == 400


async def test_pmr_companion_avec_porteur_accepte(db, catalog, today):
    # Contre-cas : disability + accompagnateur dans la même commande.
    resa = await create_reservation(
        db,
        order(
            item(
                "museum_entry",
                category="adult",
                visit_date=today,
                free_profile=FreeProfile.DISABILITY,
            ),
            item(
                "museum_entry",
                visit_date=today,
                free_profile=FreeProfile.PMR_COMPANION,
            ),
        ),
    )
    assert resa.status == "confirmed"  # panier à 0 €
    assert len(resa.tickets) == 2


async def test_addon_seul_rejete(db, catalog):
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "extra_show", category="adult",
                    session_id=catalog.session.id,
                )
            ),
        )
    assert e.value.status_code == 400


async def test_addon_sans_droit_de_base(db, catalog, today):
    # musée + séance supplémentaire : le droit théâtre n'est pas couvert.
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item("museum_entry", category="adult", visit_date=today),
                item(
                    "extra_show", category="adult",
                    session_id=catalog.session.id,
                ),
            ),
        )
    assert e.value.status_code == 400


async def test_addon_couvert_par_un_pass(db, catalog):
    # Contre-cas : le pass couvre le droit théâtre → add-on accepté.
    resa = await create_reservation(
        db,
        order(
            item(
                "pass_1_show",
                category="adult",
                visit_date=day_of(catalog.session),
                session_id=catalog.session.id,
            ),
            item(
                "extra_show", category="adult",
                session_id=catalog.session2.id,
            ),
        ),
    )
    assert resa.total_price == 25  # 20 + 5


async def test_seance_expiree_invendable(db, catalog):
    past = await add_session(db, catalog.theater, days=-2, hour=14)
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item("theater_show", category="adult", session_id=past.id)
            ),
        )
    assert e.value.status_code == 400


async def test_seance_hors_theatre(db, catalog):
    # Une séance rattachée à l'événement musée n'est pas une séance
    # de théâtre (SESSION_COMPONENT_EVENT_TYPE).
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "theater_show", category="adult",
                    session_id=catalog.museum_session.id,
                )
            ),
        )
    assert e.value.status_code == 400


async def test_seance_introuvable(db, catalog):
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "theater_show", category="adult",
                    session_id=uuid.uuid4(),
                )
            ),
        )
    assert e.value.status_code == 404


async def test_capacite_insuffisante(db, catalog):
    cap1 = await add_session(db, catalog.theater, days=11, hour=14, cap=1)
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "theater_show", category="adult",
                    session_id=cap1.id,
                ),
                item(
                    "theater_show", category="child",
                    session_id=cap1.id,
                ),
            ),
        )
    assert e.value.status_code == 400


async def test_evenement_inactif(db, catalog):
    inactive = await add_session(db, catalog.inactive_theater, days=10)
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "theater_show", category="adult",
                    session_id=inactive.id,
                )
            ),
        )
    assert e.value.status_code == 400


async def test_categorie_manquante_produit_simple(db, catalog, today):
    # Sans free_profile ni category, impossible de déterminer les
    # personnes couvertes par l'item.
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db, order(item("museum_entry", visit_date=today))
        )
    assert e.value.status_code == 400
    assert "Catégorie tarifaire requise" in e.value.detail


async def test_pass_visit_date_incoherente(db, catalog):
    # Pass « Journée » : la séance choisie doit être le jour de visite.
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "pass_1_show", category="adult",
                    visit_date=paris_today() + timedelta(days=3),
                    session_id=catalog.session.id,
                )
            ),
        )
    assert e.value.status_code == 400
    assert "ne coïncide pas" in e.value.detail


async def test_produit_sans_aucun_droit(db, catalog, today):
    # Catalogue corrompu : produit vendable qui n'ouvre ni journée
    # musée ni séance — rien à dater, refus explicite.
    await add_product(
        db,
        code="empty_product",
        label="Produit vide",
        kind=ProductKind.SIMPLE,
        price_adult=Decimal("5.00"),
        components=[],
    )
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db, order(item("empty_product", category="adult"))
        )
    assert e.value.status_code == 400
    assert "ni journée ni séance" in e.value.detail


async def test_composant_musee_sans_evenement(db, catalog, today):
    # Catalogue corrompu : droit musée sans événement rattaché.
    await add_product(
        db,
        code="museum_no_event",
        label="Musée sans événement",
        kind=ProductKind.SIMPLE,
        price_adult=Decimal("5.00"),
        components=[
            ProductComponent(
                component_type=ComponentType.MUSEUM_DAY,
                quantity=1,
                event_id=None,
            )
        ],
    )
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db, order(item("museum_no_event", category="adult", visit_date=today))
        )
    assert e.value.status_code == 400
    assert "mal configuré" in e.value.detail


async def test_evenement_musee_inactif(db, catalog, today):
    musee_off = await add_event(
        db, "Musée fermé (pytest)", EventType.PERMANENT_EXHIBITION,
        is_active=False,
    )
    await add_product(
        db,
        code="museum_off",
        label="Musée inactif",
        kind=ProductKind.SIMPLE,
        price_adult=Decimal("5.00"),
        components=[
            ProductComponent(
                component_type=ComponentType.MUSEUM_DAY,
                quantity=1,
                event_id=musee_off.id,
            )
        ],
    )
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db, order(item("museum_off", category="adult", visit_date=today))
        )
    assert e.value.status_code == 400
    assert "inactif ou introuvable" in e.value.detail


async def test_groupe_sans_prix_par_personne(db, catalog, today):
    # Catalogue corrompu : produit groupe sans tarif unitaire.
    await add_product(
        db,
        code="group_no_price",
        label="Groupe sans prix",
        kind=ProductKind.GROUP,
        price_adult=None,
        components=[museum_comp(catalog.musee)],
    )
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db, order(item("group_no_price", group_size=8, visit_date=today))
        )
    assert e.value.status_code == 400
    assert "prix par personne manquant" in e.value.detail
