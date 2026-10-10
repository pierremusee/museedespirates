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
from sqlalchemy import func, select

from app.models import (
    ComponentType,
    EventType,
    FreeProfile,
    ProductComponent,
    ProductKind,
    Reservation,
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
    theater_comp,
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


async def test_pmr_deux_porteurs_deux_accompagnateurs(db, catalog, today):
    # Ratio 1:1 respecté en agrégé : 2 porteurs, 2 accompagnateurs.
    resa = await create_reservation(
        db,
        order(
            *[
                item(
                    "museum_entry",
                    category="adult",
                    visit_date=today,
                    free_profile=FreeProfile.DISABILITY,
                )
                for _ in range(2)
            ],
            *[
                item(
                    "museum_entry",
                    visit_date=today,
                    free_profile=FreeProfile.PMR_COMPANION,
                )
                for _ in range(2)
            ],
        ),
    )
    assert resa.status == "confirmed"
    assert len(resa.tickets) == 4


async def test_pmr_deux_accompagnateurs_un_porteur_rejetes(
    db, catalog, today
):
    # Plafond : 2 accompagnateurs pour 1 seul porteur → refus, même
    # quand la co-présence est parfaite.
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "museum_entry",
                    category="adult",
                    visit_date=today,
                    free_profile=FreeProfile.DISABILITY,
                ),
                *[
                    item(
                        "museum_entry",
                        visit_date=today,
                        free_profile=FreeProfile.PMR_COMPANION,
                    )
                    for _ in range(2)
                ],
            ),
        )
    assert e.value.status_code == 400
    assert "accompagnateur" in e.value.detail


async def test_pmr_meme_seance_accepte(db, catalog):
    # Porteur et accompagnateur sur la même séance : co-présence
    # vérifiée par le session_id, pas par le produit.
    resa = await create_reservation(
        db,
        order(
            item(
                "theater_show",
                category="adult",
                session_id=catalog.session.id,
                free_profile=FreeProfile.DISABILITY,
            ),
            item(
                "theater_show",
                session_id=catalog.session.id,
                free_profile=FreeProfile.PMR_COMPANION,
            ),
        ),
    )
    assert resa.status == "confirmed"


async def test_pmr_copresence_partielle(db, catalog):
    # Un seul point commun suffit : le porteur a un pass musée+séance,
    # l'accompagnateur ne prend que la séance — même session_id partagé.
    resa = await create_reservation(
        db,
        order(
            item(
                "pass_1_show",
                category="adult",
                visit_date=day_of(catalog.session),
                session_id=catalog.session.id,
                free_profile=FreeProfile.DISABILITY,
            ),
            item(
                "theater_show",
                session_id=catalog.session.id,
                free_profile=FreeProfile.PMR_COMPANION,
            ),
        ),
    )
    assert resa.status == "confirmed"


async def test_pmr_seances_differentes_rejete(db, catalog):
    # Ratio respecté mais aucune séance ni visite commune : l'accompagnateur
    # n'accompagne personne → refus.
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "theater_show",
                    category="adult",
                    session_id=catalog.session.id,
                    free_profile=FreeProfile.DISABILITY,
                ),
                item(
                    "theater_show",
                    session_id=catalog.session2.id,
                    free_profile=FreeProfile.PMR_COMPANION,
                ),
            ),
        )
    assert e.value.status_code == 400
    assert "partager" in e.value.detail


async def test_pmr_visites_jours_differents_rejete(db, catalog, today):
    # Pour un accès musée (sans séance), la « séance » est le jour de
    # visite : deux dates différentes ne sont pas une co-présence.
    with pytest.raises(HTTPException) as e:
        await create_reservation(
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
                    visit_date=today + timedelta(days=1),
                    free_profile=FreeProfile.PMR_COMPANION,
                ),
            ),
        )
    assert e.value.status_code == 400
    assert "partager" in e.value.detail


async def test_addon_disability_ne_cree_pas_de_porteur(db, catalog):
    # Régression (faux négatif corrigé) : l'add-on du porteur est un
    # accès, pas une personne — il ne peut pas couvrir un second
    # accompagnateur.
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "theater_show",
                    category="adult",
                    session_id=catalog.session.id,
                    free_profile=FreeProfile.DISABILITY,
                ),
                item(
                    "extra_show",
                    category="adult",
                    session_id=catalog.session2.id,
                    free_profile=FreeProfile.DISABILITY,
                ),
                *[
                    item(
                        "theater_show",
                        session_id=catalog.session.id,
                        free_profile=FreeProfile.PMR_COMPANION,
                    )
                    for _ in range(2)
                ],
            ),
        )
    assert e.value.status_code == 400
    assert "accompagnateur" in e.value.detail


async def test_pmr_rejet_sans_effet_de_bord(db, catalog):
    # Un refus ne crée ni réservation ni consommation de capacité.
    before_seats = (
        await db.execute(
            select(Session.booked_seats).where(
                Session.id == catalog.session.id
            )
        )
    ).scalar_one()
    before_resa = (
        await db.execute(select(func.count(Reservation.id)))
    ).scalar_one()
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "theater_show",
                    category="adult",
                    session_id=catalog.session.id,
                    free_profile=FreeProfile.DISABILITY,
                ),
                *[
                    item(
                        "theater_show",
                        session_id=catalog.session.id,
                        free_profile=FreeProfile.PMR_COMPANION,
                    )
                    for _ in range(2)
                ],
            ),
        )
    assert e.value.status_code == 400
    assert (
        await db.execute(
            select(Session.booked_seats).where(
                Session.id == catalog.session.id
            )
        )
    ).scalar_one() == before_seats
    assert (
        await db.execute(select(func.count(Reservation.id)))
    ).scalar_one() == before_resa


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


# --- Couverture des add-ons : identité de la personne (P1) --------------
#
# Un produit add-on s'achète « pour une personne » : la couverture se
# vérifie par triplet (type de droit, catégorie tarifaire, profil de
# gratuité) — la même identité que la fusion des billets. Avant la
# correction, les compteurs n'étaient clés que par type de droit : un
# billet adulte couvrait un supplément enfant (régression P1).


async def test_addon_enfants_non_couverts_par_adultes(db, catalog):
    # Scénario P1 rapporté : 6 billets théâtre adultes + 6 suppléments
    # enfants acceptés à 81 € alors qu'aucun enfant n'a de billet de
    # base. Rejeté avant tout effet de bord.
    sess_a = await add_session(db, catalog.theater, days=10, hour=14, cap=20)
    sess_b = await add_session(db, catalog.theater, days=10, hour=18, cap=20)
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                *[
                    item(
                        "theater_show", category="adult",
                        session_id=sess_a.id,
                    )
                    for _ in range(6)
                ],
                *[
                    item(
                        "extra_show", category="child",
                        session_id=sess_b.id,
                    )
                    for _ in range(6)
                ],
            ),
        )
    assert e.value.status_code == 400
    assert "complémentaire" in e.value.detail
    # Aucune donnée incohérente : jauge intacte, aucune commande créée.
    await db.refresh(sess_a)
    await db.refresh(sess_b)
    assert sess_a.booked_seats == 0
    assert sess_b.booked_seats == 0
    n_resas = (
        await db.execute(select(func.count(Reservation.id)))
    ).scalar_one()
    assert n_resas == 0


async def test_addon_couvert_par_meme_categorie(db, catalog):
    resa = await create_reservation(
        db,
        order(
            item(
                "theater_show", category="child",
                session_id=catalog.session.id,
            ),
            item(
                "extra_show", category="child",
                session_id=catalog.session2.id,
            ),
        ),
    )
    assert resa.total_price == Decimal("10.50")  # 7 + 3.50


async def test_commande_mixte_categories_couvertes(db, catalog):
    resa = await create_reservation(
        db,
        order(
            item(
                "theater_show", category="adult",
                session_id=catalog.session.id,
            ),
            item(
                "theater_show", category="child",
                session_id=catalog.session.id,
            ),
            item(
                "extra_show", category="adult",
                session_id=catalog.session2.id,
            ),
            item(
                "extra_show", category="child",
                session_id=catalog.session2.id,
            ),
        ),
    )
    assert resa.total_price == Decimal("25.50")  # 10 + 7 + 5 + 3.50


async def test_addon_reduit_non_couvert_par_adulte(db, catalog):
    # La catégorie tarifaire est dimensionnante : un billet adulte ne
    # couvre pas un supplément au tarif réduit.
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "theater_show", category="adult",
                    session_id=catalog.session.id,
                ),
                item(
                    "extra_show", category="reduced",
                    session_id=catalog.session2.id,
                ),
            ),
        )
    assert e.value.status_code == 400


async def test_couverture_partielle_mixte_rejetee(db, catalog):
    # 2 adultes + 1 enfant en base : 2 suppléments enfants dépassent la
    # couverture enfant même si le total des personnes est couvert.
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                *[
                    item(
                        "theater_show", category="adult",
                        session_id=catalog.session.id,
                    )
                    for _ in range(2)
                ],
                item(
                    "theater_show", category="child",
                    session_id=catalog.session.id,
                ),
                *[
                    item(
                        "extra_show", category="adult",
                        session_id=catalog.session2.id,
                    )
                    for _ in range(2)
                ],
                *[
                    item(
                        "extra_show", category="child",
                        session_id=catalog.session2.id,
                    )
                    for _ in range(2)
                ],
            ),
        )
    assert e.value.status_code == 400


async def test_addon_gratuit_non_couvert_par_base_payante(db, catalog):
    # Un billet de base payant adulte ne couvre pas le supplément d'un
    # accompagnateur PMR — le profil de gratuité fait partie de
    # l'identité de la personne.
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "theater_show", category="adult",
                    session_id=catalog.session.id,
                ),
                item(
                    "extra_show",
                    session_id=catalog.session2.id,
                    free_profile=FreeProfile.PMR_COMPANION,
                ),
            ),
        )
    assert e.value.status_code == 400
    assert "complémentaire" in e.value.detail


async def test_addon_under4_non_couvert_par_enfant_payant(db, catalog):
    # Un enfant payant et un moins-de-4-ans sont deux personnes
    # distinctes : le billet de base de l'un ne couvre pas le
    # supplément de l'autre.
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "theater_show", category="child",
                    session_id=catalog.session.id,
                ),
                item(
                    "extra_show",
                    session_id=catalog.session2.id,
                    free_profile=FreeProfile.UNDER_4,
                ),
            ),
        )
    assert e.value.status_code == 400


async def test_addon_under4_couvert_par_base_under4(db, catalog):
    # Contre-cas : l'enfant de moins de 4 ans porte son propre billet
    # de base (gratuit) — le supplément est couvert, panier à 0 €.
    resa = await create_reservation(
        db,
        order(
            item(
                "theater_show",
                session_id=catalog.session.id,
                free_profile=FreeProfile.UNDER_4,
            ),
            item(
                "extra_show",
                session_id=catalog.session2.id,
                free_profile=FreeProfile.UNDER_4,
            ),
        ),
    )
    assert resa.status == "confirmed"  # panier à 0 €
    assert resa.total_price == Decimal(0)


async def test_addon_disability_autre_categorie_rejete(db, catalog):
    # Le profil disability conserve sa catégorie physique : une base
    # « disability enfant » ne couvre pas un supplément « disability
    # adulte ».
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "theater_show", category="child",
                    session_id=catalog.session.id,
                    free_profile=FreeProfile.DISABILITY,
                ),
                item(
                    "extra_show", category="adult",
                    session_id=catalog.session2.id,
                    free_profile=FreeProfile.DISABILITY,
                ),
            ),
        )
    assert e.value.status_code == 400


async def test_addon_disability_meme_categorie_couvert(db, catalog):
    resa = await create_reservation(
        db,
        order(
            item(
                "theater_show", category="adult",
                session_id=catalog.session.id,
                free_profile=FreeProfile.DISABILITY,
            ),
            item(
                "extra_show", category="adult",
                session_id=catalog.session2.id,
                free_profile=FreeProfile.DISABILITY,
            ),
        ),
    )
    assert resa.status == "confirmed"  # panier à 0 €


async def test_pmr_addon_seance_supp_sans_porteur_rejete(db, catalog):
    # Régression A1 : la co-présence « au moins un point commun »
    # laissait l'accompagnateur emporter un accès gratuit à une séance
    # sans aucun porteur. Désormais CHAQUE accès de l'accompagnateur
    # exige un porteur disposant du même accès — ici S2 manque.
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "theater_show", category="adult",
                    session_id=catalog.session.id,
                    free_profile=FreeProfile.DISABILITY,
                ),
                item(
                    "theater_show",
                    session_id=catalog.session.id,
                    free_profile=FreeProfile.PMR_COMPANION,
                ),
                item(
                    "extra_show",
                    session_id=catalog.session2.id,
                    free_profile=FreeProfile.PMR_COMPANION,
                ),
            ),
        )
    assert e.value.status_code == 400
    assert "partager" in e.value.detail


async def test_pmr_addon_seance_supp_avec_porteur_accepte(db, catalog):
    # Contre-cas : le porteur dispose aussi de S2 (via un Pass 2
    # spectacles) — la co-présence de l'accompagnateur sur S2 est
    # établie par son propre add-on, tous ses accès sont couverts.
    day = day_of(catalog.session)
    resa = await create_reservation(
        db,
        order(
            item(
                "pass_2_shows", category="adult",
                visit_date=day,
                session_ids=[catalog.session.id, catalog.session2.id],
                free_profile=FreeProfile.DISABILITY,
            ),
            item(
                "theater_show",
                session_id=catalog.session.id,
                free_profile=FreeProfile.PMR_COMPANION,
            ),
            item(
                "extra_show",
                session_id=catalog.session2.id,
                free_profile=FreeProfile.PMR_COMPANION,
            ),
        ),
    )
    assert resa.status == "confirmed"  # panier à 0 €
    assert len(resa.tickets) == 2
    assert sorted(len(t.accesses) for t in resa.tickets) == [2, 3]


async def test_addons_miroir_porteur_et_accompagnateur(db, catalog):
    # Flux nominal des interfaces : le porteur et l'accompagnateur ont
    # chacun leur séance supplémentaire — ratio respecté sur les lignes
    # de base, accès fusionnés par personne.
    resa = await create_reservation(
        db,
        order(
            item(
                "theater_show", category="adult",
                session_id=catalog.session.id,
                free_profile=FreeProfile.DISABILITY,
            ),
            item(
                "theater_show",
                session_id=catalog.session.id,
                free_profile=FreeProfile.PMR_COMPANION,
            ),
            item(
                "extra_show", category="adult",
                session_id=catalog.session2.id,
                free_profile=FreeProfile.DISABILITY,
            ),
            item(
                "extra_show",
                session_id=catalog.session2.id,
                free_profile=FreeProfile.PMR_COMPANION,
            ),
        ),
    )
    assert resa.status == "confirmed"
    assert len(resa.tickets) == 2
    assert sorted(len(t.accesses) for t in resa.tickets) == [2, 2]


async def test_addon_famille_couvert_par_forfait(db, catalog):
    # Un forfait famille à séance couvre ses membres par catégorie
    # physique : 2 adultes + 2 enfants (+ extra_children).
    await add_product(
        db,
        code="family_show",
        label="Forfait Famille — Spectacle (pytest)",
        kind=ProductKind.FAMILY,
        family_base_price=Decimal("40.00"),
        extra_child_price=Decimal("5.00"),
        components=[theater_comp()],
    )
    resa = await create_reservation(
        db,
        order(
            item("family_show", session_id=catalog.session.id),
            *[
                item(
                    "extra_show", category="adult",
                    session_id=catalog.session2.id,
                )
                for _ in range(2)
            ],
            *[
                item(
                    "extra_show", category="child",
                    session_id=catalog.session2.id,
                )
                for _ in range(2)
            ],
        ),
    )
    assert resa.total_price == Decimal("57.00")  # 40 + 2x5 + 2x3.50


async def test_addon_famille_musee_seul_rejete(db, catalog, today):
    # Le forfait famille « musée seul » n'accorde pas le droit séance :
    # les suppléments de ses membres restent refusés.
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item("family_museum", visit_date=today),
                item(
                    "extra_show", category="child",
                    session_id=catalog.session.id,
                ),
            ),
        )
    assert e.value.status_code == 400


async def test_addon_groupe_couvert_par_groupe(db, catalog):
    # Un produit add-on de kind=group est couvert par un produit de
    # base de kind=group accordant le même droit.
    await add_product(
        db,
        code="group_show",
        label="Séance groupe (pytest)",
        kind=ProductKind.GROUP,
        price_adult=Decimal("6.00"),
        components=[theater_comp()],
    )
    await add_product(
        db,
        code="group_extra",
        label="Séance supp. groupe (pytest)",
        kind=ProductKind.GROUP,
        is_addon=True,
        price_adult=Decimal("2.00"),
        components=[theater_comp()],
    )
    resa = await create_reservation(
        db,
        order(
            item(
                "group_show", group_size=10,
                session_id=catalog.session.id,
            ),
            item(
                "group_extra", group_size=8,
                session_id=catalog.session2.id,
            ),
        ),
    )
    assert resa.total_price == Decimal("76.00")  # 10x6 + 8x2


async def test_addon_groupe_non_couvert_par_individuels(db, catalog, today):
    # Des billets individuels ne couvrent pas un add-on groupe : les
    # personnes groupe portent la catégorie « group ».
    await add_product(
        db,
        code="group_museum_extra",
        label="Journée musée supp. groupe (pytest)",
        kind=ProductKind.GROUP,
        is_addon=True,
        price_adult=Decimal("2.00"),
        components=[museum_comp(catalog.musee)],
    )
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                *[
                    item("museum_entry", category="adult", visit_date=today)
                    for _ in range(8)
                ],
                item(
                    "group_museum_extra", group_size=8, visit_date=today
                ),
            ),
        )
    assert e.value.status_code == 400


async def test_plusieurs_addons_differents_couverts(db, catalog):
    # Deux add-ons de droits différents pour la même personne : chacun
    # est couvert par le droit correspondant du pass. La journée musée
    # supplémentaire vise un AUTRE jour que le pass — au même jour, ce
    # serait un doublon du droit déjà accordé (refusé).
    await add_product(
        db,
        code="museum_extra",
        label="Journée musée supp. (pytest)",
        kind=ProductKind.SIMPLE,
        is_addon=True,
        price_adult=Decimal("3.00"),
        price_child=Decimal("2.00"),
        price_reduced=Decimal("2.00"),
        components=[museum_comp(catalog.musee)],
    )
    day = day_of(catalog.session)
    resa = await create_reservation(
        db,
        order(
            item(
                "pass_1_show", category="adult",
                visit_date=day, session_id=catalog.session.id,
            ),
            item(
                "extra_show", category="adult",
                session_id=catalog.session2.id,
            ),
            item(
                "museum_extra", category="adult",
                visit_date=day + timedelta(days=1),
            ),
        ),
    )
    assert resa.total_price == Decimal("28.00")  # 20 + 5 + 3


async def test_deux_addons_meme_droit_non_empilables(db, catalog):
    # Une couverture = une unité par personne : une seule personne ne
    # peut empiler deux suppléments du même droit.
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "theater_show", category="adult",
                    session_id=catalog.session.id,
                ),
                *[
                    item(
                        "extra_show", category="adult",
                        session_id=catalog.session2.id,
                    )
                    for _ in range(2)
                ],
            ),
        )
    assert e.value.status_code == 400


# --- Invariant « un add-on étend une personne existante » (B1/A1) ---
#
# La couverture par droit (ci-dessus) ne suffit pas : l'émission
# fusionne les accès par groupe (catégorie, profil de gratuité) et une
# ligne add-on visant un accès déjà accordé y créait un billet-
# personne surnuméraire — second QR valide pour la même séance, jauge
# décomptée en double, second accompagnateur PMR gratuit. La fusion
# est désormais simulée à la commande (produits de base d'abord,
# add-ons ensuite) et toute personne add-on sans billet d'accueil est
# refusée — jamais de deuxième billet valide pour un accès déjà détenu.


async def test_addon_meme_seance_rejette(db, catalog):
    # Régression reproduite par HTTP sur main : billet de base S1 +
    # add-on S1 émettait 2 billets adultes valides pour S1 et
    # consommait 2 places de jauge pour une seule personne.
    before_seats = (
        await db.execute(
            select(Session.booked_seats).where(
                Session.id == catalog.session.id
            )
        )
    ).scalar_one()
    before_resa = (
        await db.execute(select(func.count(Reservation.id)))
    ).scalar_one()
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "theater_show", category="adult",
                    session_id=catalog.session.id,
                ),
                item(
                    "extra_show", category="adult",
                    session_id=catalog.session.id,
                ),
            ),
        )
    assert e.value.status_code == 400
    assert "complémentaire" in e.value.detail
    # Rejet net : ni réservation, ni billet, ni place consommée.
    assert (
        await db.execute(
            select(Session.booked_seats).where(
                Session.id == catalog.session.id
            )
        )
    ).scalar_one() == before_seats
    assert (
        await db.execute(select(func.count(Reservation.id)))
    ).scalar_one() == before_resa


async def test_addon_meme_seance_deux_personnes_accepte(db, catalog):
    # La même séance peut être accordée à DEUX personnes différentes —
    # le repère n'est pas la séance dupliquée mais l'accès déjà détenu
    # par la même personne. A (pass : musée + S1) et B (musée seul) :
    # le supplément S1 complète le billet de B — 2 billets, 2 places
    # décomptées sur S1.
    # Profil gratuit → panier à 0 € → billets émis immédiatement.
    day = day_of(catalog.session)
    resa = await create_reservation(
        db,
        order(
            item(
                "pass_1_show", category="adult",
                visit_date=day, session_id=catalog.session.id,
                free_profile=FreeProfile.DISABILITY,
            ),
            item(
                "museum_entry", category="adult", visit_date=day,
                free_profile=FreeProfile.DISABILITY,
            ),
            item(
                "extra_show", category="adult",
                session_id=catalog.session.id,
                free_profile=FreeProfile.DISABILITY,
            ),
        ),
    )
    assert resa.status == "confirmed"
    assert len(resa.tickets) == 2
    assert sorted(len(t.accesses) for t in resa.tickets) == [2, 2]
    s1 = (
        await db.execute(
            select(Session.booked_seats).where(
                Session.id == catalog.session.id
            )
        )
    ).scalar_one()
    assert s1 == 2  # deux personnes distinctes sur S1


async def test_addon_seance_deja_dans_le_pass_rejete(db, catalog):
    # Même invariant sur un produit pass : pass_1_show accorde déjà
    # S1 — le supplément S1 ne peut pas dupliquer l'accès.
    day = day_of(catalog.session)
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "pass_1_show", category="adult",
                    visit_date=day, session_id=catalog.session.id,
                ),
                item(
                    "extra_show", category="adult",
                    session_id=catalog.session.id,
                ),
            ),
        )
    assert e.value.status_code == 400
    assert "complémentaire" in e.value.detail


async def test_deux_billets_meme_seance_plus_addon_rejete(db, catalog):
    # 2 personnes sur S1 + supplément S1 : toutes les personnes du
    # groupe ont déjà l'accès — l'add-on créerait une 3e personne.
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                *[
                    item(
                        "theater_show", category="adult",
                        session_id=catalog.session.id,
                    )
                    for _ in range(2)
                ],
                item(
                    "extra_show", category="adult",
                    session_id=catalog.session.id,
                ),
            ),
        )
    assert e.value.status_code == 400
    assert "complémentaire" in e.value.detail


async def test_pmr_addon_meme_seance_rejette(db, catalog):
    # Régression B1 : porteur + accompagnateur sur S1 + add-on
    # pmr_companion sur S1 — l'émission produisait un second billet
    # accompagnateur : 3 billets gratuits, 3 places décomptées pour
    # 2 personnes.
    before_seats = (
        await db.execute(
            select(Session.booked_seats).where(
                Session.id == catalog.session.id
            )
        )
    ).scalar_one()
    before_resa = (
        await db.execute(select(func.count(Reservation.id)))
    ).scalar_one()
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "theater_show", category="adult",
                    session_id=catalog.session.id,
                    free_profile=FreeProfile.DISABILITY,
                ),
                item(
                    "theater_show",
                    session_id=catalog.session.id,
                    free_profile=FreeProfile.PMR_COMPANION,
                ),
                item(
                    "extra_show",
                    session_id=catalog.session.id,
                    free_profile=FreeProfile.PMR_COMPANION,
                ),
            ),
        )
    assert e.value.status_code == 400
    assert "complémentaire" in e.value.detail
    assert (
        await db.execute(
            select(Session.booked_seats).where(
                Session.id == catalog.session.id
            )
        )
    ).scalar_one() == before_seats
    assert (
        await db.execute(select(func.count(Reservation.id)))
    ).scalar_one() == before_resa


async def test_pmr_copresence_addon_seul_ne_couvre_pas_le_reste(
    db, catalog,
):
    # La co-présence apportée par un add-on ne rachète pas les accès
    # de la ligne de base : chaque accès de l'accompagnateur exige un
    # porteur. Ici l'add-on S1 est bien partagé, mais la séance S2 de
    # la ligne de base ne l'est pas → refus.
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item(
                    "theater_show", category="adult",
                    session_id=catalog.session.id,
                    free_profile=FreeProfile.DISABILITY,
                ),
                item(
                    "theater_show",
                    session_id=catalog.session2.id,
                    free_profile=FreeProfile.PMR_COMPANION,
                ),
                item(
                    "extra_show",
                    session_id=catalog.session.id,
                    free_profile=FreeProfile.PMR_COMPANION,
                ),
            ),
        )
    assert e.value.status_code == 400
    assert "partager" in e.value.detail


async def test_addon_musee_meme_jour_rejete(db, catalog, today):
    # Deux journées musée au même événement le même jour = doublon du
    # droit — l'add-on créerait une personne supplémentaire.
    await add_product(
        db,
        code="museum_extra",
        label="Journée musée supp. (pytest)",
        kind=ProductKind.SIMPLE,
        is_addon=True,
        price_adult=Decimal("3.00"),
        components=[museum_comp(catalog.musee)],
    )
    with pytest.raises(HTTPException) as e:
        await create_reservation(
            db,
            order(
                item("museum_entry", category="adult", visit_date=today),
                item(
                    "museum_extra", category="adult", visit_date=today
                ),
            ),
        )
    assert e.value.status_code == 400
    assert "complémentaire" in e.value.detail


async def test_addon_musee_autre_jour_fusionne(db, catalog, today):
    # À un autre jour, la journée musée supplémentaire est un droit
    # distinct (la date fait partie de la clé d'accès) : elle fusionne
    # sur le même billet — 1 QR, 2 accès musée datés différemment.
    await add_product(
        db,
        code="museum_extra",
        label="Journée musée supp. (pytest)",
        kind=ProductKind.SIMPLE,
        is_addon=True,
        price_adult=Decimal("3.00"),
        price_child=Decimal("2.00"),
        price_reduced=Decimal("2.00"),
        components=[museum_comp(catalog.musee)],
    )
    resa = await create_reservation(
        db,
        order(
            item(
                "museum_entry",
                visit_date=today,
                free_profile=FreeProfile.UNDER_4,
            ),
            item(
                "museum_extra",
                visit_date=today + timedelta(days=1),
                free_profile=FreeProfile.UNDER_4,
            ),
        ),
    )
    assert resa.status == "confirmed"  # panier à 0 €
    assert len(resa.tickets) == 1
    assert len(resa.tickets[0].accesses) == 2
