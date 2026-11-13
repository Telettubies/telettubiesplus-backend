"""HU-B02: invitar un usuario a la organización (RF-09, S-05, R-15)."""

import uuid
from dataclasses import dataclass, field

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.integrations import supabase
from analizador.models import AuditoriaMiembro, Miembro
from analizador.models.enums import AccionMiembro, Rol
from tests.apoyo import agregar_miembro, crear_organizacion


@dataclass
class SupabaseFalso:
    """Sustituye a la API de administración de Supabase y anota cada llamada."""

    invitados: list[str] = field(default_factory=list)
    eliminados: list[uuid.UUID] = field(default_factory=list)
    usuario_id: uuid.UUID = field(default_factory=uuid.uuid4)
    error: Exception | None = None

    async def invitar_usuario(self, email: str) -> uuid.UUID:
        if self.error is not None:
            raise self.error
        self.invitados.append(email)
        return self.usuario_id

    async def eliminar_usuario(self, usuario_id: uuid.UUID) -> None:
        self.eliminados.append(usuario_id)


@pytest.fixture
def supabase_falso(monkeypatch: pytest.MonkeyPatch) -> SupabaseFalso:
    falso = SupabaseFalso()
    monkeypatch.setattr(supabase, "invitar_usuario", falso.invitar_usuario)
    monkeypatch.setattr(supabase, "eliminar_usuario", falso.eliminar_usuario)
    return falso


async def _miembros(sesion: AsyncSession, organizacion_id: uuid.UUID) -> list[Miembro]:
    sesion.expire_all()
    return list(
        await sesion.scalars(select(Miembro).where(Miembro.organizacion_id == organizacion_id))
    )


# --- Criterio 1: la invitación crea el miembro en la organización del administrador ----------


@pytest.mark.parametrize("rol", [Rol.ADMINISTRADOR, Rol.REVISOR])
async def test_el_administrador_invita_con_un_rol_y_se_crea_el_miembro(
    cliente: httpx.AsyncClient, sesion: AsyncSession, supabase_falso: SupabaseFalso, rol: Rol
) -> None:
    admin = await crear_organizacion(sesion)

    respuesta = await cliente.post(
        "/api/miembros/invitaciones",
        headers=admin.cabeceras,
        json={"email": "nueva@empresa.co", "rol": rol.value},
    )

    assert respuesta.status_code == 201
    assert supabase_falso.invitados == ["nueva@empresa.co"]
    datos = respuesta.json()
    assert datos["usuario_id"] == str(supabase_falso.usuario_id)
    assert datos["organizacion_id"] == str(admin.organizacion_id)
    assert datos["rol"] == rol.value
    assert datos["invitacion_pendiente"] is True


# --- Criterio 2: invitación y miembro se crean juntos o ninguno ----------------------------


async def test_si_no_se_guarda_el_miembro_se_deshace_la_invitacion(
    cliente: httpx.AsyncClient, sesion: AsyncSession, supabase_falso: SupabaseFalso
) -> None:
    admin = await crear_organizacion(sesion)
    # Supabase devuelve un usuario que ya es miembro: guardar el nuevo miembro viola la unicidad.
    supabase_falso.usuario_id = admin.usuario_id

    respuesta = await cliente.post(
        "/api/miembros/invitaciones", headers=admin.cabeceras, json={"email": "otra@empresa.co"}
    )

    assert respuesta.status_code == 503
    assert supabase_falso.eliminados == [admin.usuario_id]
    assert len(await _miembros(sesion, admin.organizacion_id)) == 1
    auditoria = await sesion.scalars(select(AuditoriaMiembro))
    assert list(auditoria) == []


async def test_si_supabase_falla_no_se_crea_el_miembro(
    cliente: httpx.AsyncClient, sesion: AsyncSession, supabase_falso: SupabaseFalso
) -> None:
    admin = await crear_organizacion(sesion)
    supabase_falso.error = supabase.SupabaseError("Supabase respondió 500 al invitar")

    respuesta = await cliente.post(
        "/api/miembros/invitaciones", headers=admin.cabeceras, json={"email": "otra@empresa.co"}
    )

    assert respuesta.status_code == 502
    assert "500" not in respuesta.text
    assert len(await _miembros(sesion, admin.organizacion_id)) == 1


async def test_un_correo_con_cuenta_existente_se_rechaza_con_un_mensaje_claro(
    cliente: httpx.AsyncClient, sesion: AsyncSession, supabase_falso: SupabaseFalso
) -> None:
    admin = await crear_organizacion(sesion)
    supabase_falso.error = supabase.UsuarioExistenteError("El correo ya tiene una cuenta")

    respuesta = await cliente.post(
        "/api/miembros/invitaciones", headers=admin.cabeceras, json={"email": "otra@empresa.co"}
    )

    assert respuesta.status_code == 409
    assert "ya tiene una cuenta" in respuesta.json()["detail"]
    assert len(await _miembros(sesion, admin.organizacion_id)) == 1


# --- Criterio 3: solo a su propia organización ---------------------------------------------


async def test_un_administrador_solo_invita_a_su_propia_organizacion(
    cliente: httpx.AsyncClient, sesion: AsyncSession, supabase_falso: SupabaseFalso
) -> None:
    admin_a = await crear_organizacion(sesion, "A")
    admin_b = await crear_organizacion(sesion, "B")

    ajena = await cliente.post(
        "/api/miembros/invitaciones",
        headers={**admin_a.cabeceras, "X-Organizacion-Id": str(admin_b.organizacion_id)},
        json={"email": "intruso@empresa.co", "organizacion_id": str(admin_b.organizacion_id)},
    )
    assert ajena.status_code == 403

    propia = await cliente.post(
        "/api/miembros/invitaciones",
        headers=admin_a.cabeceras,
        json={"email": "nueva@empresa.co", "organizacion_id": str(admin_b.organizacion_id)},
    )
    assert propia.status_code == 201
    assert propia.json()["organizacion_id"] == str(admin_a.organizacion_id)
    assert len(await _miembros(sesion, admin_b.organizacion_id)) == 1
    assert supabase_falso.invitados == ["nueva@empresa.co"]


# --- Criterio 4: un revisor no puede invitar -----------------------------------------------


async def test_un_revisor_no_puede_invitar(
    cliente: httpx.AsyncClient, sesion: AsyncSession, supabase_falso: SupabaseFalso
) -> None:
    admin = await crear_organizacion(sesion)
    revisor = await agregar_miembro(sesion, admin.organizacion_id, Rol.REVISOR)

    respuesta = await cliente.post(
        "/api/miembros/invitaciones", headers=revisor.cabeceras, json={"email": "x@empresa.co"}
    )

    assert respuesta.status_code == 403
    assert supabase_falso.invitados == []


# --- Criterio 5: un correo que ya es miembro no se duplica ---------------------------------


async def test_invitar_a_un_miembro_existente_es_un_error_claro_sin_duplicar(
    cliente: httpx.AsyncClient, sesion: AsyncSession, supabase_falso: SupabaseFalso
) -> None:
    admin = await crear_organizacion(sesion)
    [miembro] = await _miembros(sesion, admin.organizacion_id)

    respuesta = await cliente.post(
        "/api/miembros/invitaciones",
        headers=admin.cabeceras,
        json={"email": miembro.email.upper()},
    )

    assert respuesta.status_code == 409
    assert "ya es miembro" in respuesta.json()["detail"]
    assert supabase_falso.invitados == []
    assert len(await _miembros(sesion, admin.organizacion_id)) == 1


# --- Calidad: queda registrado quién invitó, a quién y cuándo ------------------------------


async def test_la_invitacion_queda_registrada_con_autor_y_fecha(
    cliente: httpx.AsyncClient, sesion: AsyncSession, supabase_falso: SupabaseFalso
) -> None:
    admin = await crear_organizacion(sesion)

    await cliente.post(
        "/api/miembros/invitaciones",
        headers=admin.cabeceras,
        json={"email": "nueva@empresa.co", "rol": "revisor"},
    )

    [registro] = list(await sesion.scalars(select(AuditoriaMiembro)))
    assert registro.accion == AccionMiembro.INVITACION
    assert registro.organizacion_id == admin.organizacion_id
    assert registro.actor_usuario_id == admin.usuario_id
    assert registro.miembro_usuario_id == supabase_falso.usuario_id
    assert registro.email == "nueva@empresa.co"
    assert registro.rol_nuevo == Rol.REVISOR
    assert registro.creado_en is not None
