"""Persistent signer profiles and readable interface preferences."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class SignerValues(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=150)
    position: str = Field(min_length=1, max_length=1000)
    signer: str = Field(min_length=1, max_length=500)
    contact: str = Field(default='', max_length=500)

    @field_validator('*')
    @classmethod
    def printable(cls, value):
        if any(ord(char) < 32 for char in value):
            raise ValueError('Вводьте кожне поле одним рядком.')
        return value


class Signer(SignerValues):
    id: str = Field(pattern=r'^[a-f0-9]{32}$')


class SignerDefaults(BaseModel):
    general_id: str = Field(default='', pattern=r'^(?:[a-f0-9]{32})?$')
    company_id: str = Field(default='', pattern=r'^(?:[a-f0-9]{32})?$')


class SignerState(SignerDefaults):
    profiles: list[Signer] = Field(default_factory=list, max_length=200)

    @model_validator(mode='after')
    def valid_references(self):
        ids = [profile.id for profile in self.profiles]
        if len(ids) != len(set(ids)):
            raise ValueError('Повторюються ідентифікатори профілів.')
        if any(value and value not in ids for value in (self.general_id, self.company_id)):
            raise ValueError('Обраний профіль підписанта не знайдено.')
        return self


class Appearance(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text_size: Literal['normal', 'large'] = 'normal'


def _read(path: Path, model):
    if not path.exists():
        return model()
    try:
        return model.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise ValueError('Не вдалося прочитати збережені налаштування. Відновіть резервну копію.') from exc


def _write(path: Path, data: BaseModel):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(data.model_dump_json(indent=2), encoding='utf-8')
    os.replace(temporary, path)


def preferences_router(directory: Path) -> APIRouter:
    router = APIRouter(prefix='/api', tags=['preferences'])
    signers_path, appearance_path = directory / 'signers.json', directory / 'appearance.json'

    def read_signers():
        try:
            return _read(signers_path, SignerState)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get('/signers')
    def signers():
        return read_signers()

    def save(values: SignerValues, entry_id: str | None = None):
        data = read_signers()
        if entry_id and not any(profile.id == entry_id for profile in data.profiles):
            raise HTTPException(404, 'Профіль не знайдено.')
        normalized = ' '.join(values.name.casefold().split())
        if any(profile.id != entry_id and ' '.join(profile.name.casefold().split()) == normalized for profile in data.profiles):
            raise HTTPException(422, 'Профіль з такою назвою вже існує.')
        if not entry_id and len(data.profiles) >= 200:
            raise HTTPException(422, 'Можна зберегти до 200 профілів підписантів.')
        profile = Signer(id=entry_id or uuid4().hex, **values.model_dump())
        data.profiles = [old for old in data.profiles if old.id != profile.id] + [profile]
        _write(signers_path, data)
        return profile

    @router.post('/signers')
    def create(values: SignerValues):
        return save(values)

    @router.patch('/signers/{entry_id}')
    def edit(entry_id: str, values: SignerValues):
        return save(values, entry_id)

    @router.delete('/signers/{entry_id}')
    def delete(entry_id: str):
        data = read_signers()
        if not any(profile.id == entry_id for profile in data.profiles):
            raise HTTPException(404, 'Профіль не знайдено.')
        data.profiles = [profile for profile in data.profiles if profile.id != entry_id]
        if data.general_id == entry_id:
            data.general_id = ''
        if data.company_id == entry_id:
            data.company_id = ''
        _write(signers_path, data)
        return {'deleted': True}

    @router.put('/signer-defaults')
    def defaults(values: SignerDefaults):
        data = read_signers()
        try:
            data = SignerState.model_validate({**data.model_dump(), **values.model_dump()})
        except ValidationError as exc:
            raise HTTPException(422, 'Оберіть наявні профілі підписантів.') from exc
        _write(signers_path, data)
        return data

    @router.get('/preferences')
    def appearance():
        try:
            return _read(appearance_path, Appearance)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.put('/preferences')
    def update_appearance(values: Appearance):
        _write(appearance_path, values)
        return values

    return router
