import disnake
from disnake.ext import commands, tasks
import asyncio
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, "/root/antisocial")

from shared.staff import is_staff


COLOR_NEUTRAL = 0x2b2d31
COLOR_OK = 0x9EE5B4
COLOR_ERR = 0xF6C4C5
COLOR_WARN = 0xF8E3A1


ASSETS_DIR = Path("/root/antisocial/assets/banner")
BASE_BANNER = ASSETS_DIR / "base.gif"
FONT_FILE = ASSETS_DIR / "font.ttf"

DISCORD_BANNER_MAX_BYTES = 10 * 1024 * 1024


def _err_embed(desc: str) -> disnake.Embed:
    return disnake.Embed(title="—・Ошибка", description=desc, color=COLOR_ERR)


def _ok_embed(title: str, desc: str) -> disnake.Embed:
    return disnake.Embed(title=f"—・{title}", description=desc, color=COLOR_OK)


def _info_embed(title: str, desc: str) -> disnake.Embed:
    return disnake.Embed(title=f"—・{title}", description=desc, color=COLOR_NEUTRAL)


def _import_pil():
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageSequence
        return Image, ImageDraw, ImageFont, ImageSequence
    except ImportError:
        return None, None, None, None


def _detect_circle(image) -> tuple[int, int, int] | None:
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    Image, _, _, _ = _import_pil()
    if Image is None:
        return None

    img_rgb = np.array(image.convert("RGB"))
    gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)
    gray_blurred = cv2.medianBlur(gray, 5)

    h, w = gray.shape
    min_r = max(20, min(w, h) // 20)
    max_r = min(w, h) // 4

    circles = cv2.HoughCircles(
        gray_blurred,
        cv2.HOUGH_GRADIENT,
        dp=1,
        minDist=max(50, min(w, h) // 8),
        param1=100,
        param2=30,
        minRadius=min_r,
        maxRadius=max_r
    )

    if circles is None:
        return None

    circles = np.uint16(np.around(circles))
    best = None
    for c in circles[0, :]:
        cx, cy, r = int(c[0]), int(c[1]), int(c[2])
        if cy >= h or cx >= w:
            continue
        px = img_rgb[cy, cx]
        if all(p >= 240 for p in px):
            if best is None or r > best[2]:
                best = (cx, cy, r)

    return best


def _render_frame(base_frame, count: int, cx: int, cy: int, r: int, font_size: int):
    Image, ImageDraw, ImageFont, _ = _import_pil()
    frame = base_frame.convert("RGBA")
    draw = ImageDraw.Draw(frame)
    try:
        font = ImageFont.truetype(str(FONT_FILE), size=font_size)
    except Exception:
        font = ImageFont.load_default()
    text = str(count)
    bbox = font.getbbox(text)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x_draw = cx - text_w / 2 - bbox[0]
    y_draw = cy - text_h / 2 - bbox[1]
    draw.text((x_draw, y_draw), text, fill=(0, 0, 0, 255), font=font)
    return frame


def _render_gif_bytes(count: int, cx: int, cy: int, r: int, font_size: int) -> bytes:
    Image, _, _, ImageSequence = _import_pil()

    base = Image.open(str(BASE_BANNER))

    frames = []
    durations = []

    if hasattr(base, "is_animated") and base.is_animated:
        for frame in ImageSequence.Iterator(base):
            rendered = _render_frame(frame, count, cx, cy, r, font_size)
            frames.append(rendered.convert("P", palette=Image.ADAPTIVE, colors=256))
            durations.append(frame.info.get("duration", 80))
    else:
        rendered = _render_frame(base, count, cx, cy, r, font_size)
        frames.append(rendered.convert("P", palette=Image.ADAPTIVE, colors=256))
        durations.append(0)

    buf = io.BytesIO()
    if len(frames) > 1:
        frames[0].save(
            buf, format="GIF", save_all=True, append_images=frames[1:],
            duration=durations, loop=0, optimize=True, disposal=2
        )
    else:
        frames[0].save(buf, format="GIF", optimize=True)
    return buf.getvalue()


def _render_with_fit(count: int, cx: int, cy: int, r: int, font_size: int) -> bytes:
    Image, _, _, ImageSequence = _import_pil()
    raw = _render_gif_bytes(count, cx, cy, r, font_size)
    if len(raw) <= DISCORD_BANNER_MAX_BYTES:
        return raw

    base = Image.open(str(BASE_BANNER))
    if not (hasattr(base, "is_animated") and base.is_animated):
        return raw

    all_frames = list(ImageSequence.Iterator(base))
    for keep_every in (2, 3, 4):
        rendered = []
        durations = []
        for i, frame in enumerate(all_frames):
            if i % keep_every != 0:
                continue
            r_frame = _render_frame(frame, count, cx, cy, r, font_size)
            rendered.append(r_frame.convert("P", palette=Image.ADAPTIVE, colors=128))
            durations.append((frame.info.get("duration", 80)) * keep_every)
        if not rendered:
            continue
        buf = io.BytesIO()
        rendered[0].save(
            buf, format="GIF", save_all=True, append_images=rendered[1:],
            duration=durations, loop=0, optimize=True, disposal=2
        )
        data = buf.getvalue()
        if len(data) <= DISCORD_BANNER_MAX_BYTES:
            return data

    raise RuntimeError(f"GIF не помещается в {DISCORD_BANNER_MAX_BYTES // 1024 // 1024}MB даже с прореживанием.")


def _count_voice_online(guild: disnake.Guild) -> int:
    total = 0
    for vc in guild.voice_channels:
        for m in vc.members:
            if not m.bot:
                total += 1
    return total


class Banner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._lock = asyncio.Lock()
        self.update_loop.start()

    def cog_unload(self):
        try:
            self.update_loop.cancel()
        except Exception:
            pass

    async def _get_state(self):
        return await self.bot.pool.fetchrow("SELECT * FROM banner_state WHERE id = 1")

    async def _check_assets(self) -> tuple[bool, str]:
        if not BASE_BANNER.exists():
            return False, f"Нет файла `{BASE_BANNER}`. Положите туда исходный GIF-баннер."
        if not FONT_FILE.exists():
            return False, f"Нет файла `{FONT_FILE}`. Положите туда .ttf шрифт."
        return True, ""

    @tasks.loop(minutes=1)
    async def update_loop(self):
        if self._lock.locked():
            return
        async with self._lock:
            try:
                state = await self._get_state()
                if not state or not state['enabled']:
                    return
                if not state['circle_r']:
                    return
                ok, _ = await self._check_assets()
                if not ok:
                    return
                guild = self.bot.guilds[0] if self.bot.guilds else None
                if not guild:
                    return
                if "BANNER" not in guild.features:
                    print("[Banner] У сервера нет BANNER feature — пропускаю", flush=True)
                    return
                count = _count_voice_online(guild)
                if count == state['last_count']:
                    return

                def _build():
                    return _render_with_fit(
                        count,
                        state['circle_x'], state['circle_y'], state['circle_r'],
                        state['font_size'] or max(20, int(state['circle_r'] * 1.2))
                    )

                gif_bytes = await asyncio.to_thread(_build)
                try:
                    await guild.edit(banner=gif_bytes)
                    await self.bot.pool.execute(
                        "UPDATE banner_state SET last_count = $1, last_updated_at = $2, last_error = NULL WHERE id = 1",
                        count, int(time.time())
                    )
                    print(f"[Banner] Обновлён: онлайн={count}, размер={len(gif_bytes)//1024} KB", flush=True)
                except disnake.HTTPException as e:
                    err = f"HTTPException: {e}"
                    await self.bot.pool.execute(
                        "UPDATE banner_state SET last_error = $1, last_updated_at = $2 WHERE id = 1",
                        err, int(time.time())
                    )
                    print(f"[Banner] Ошибка загрузки: {e}", flush=True)
            except Exception as e:
                import traceback
                print(f"[Banner:loop] {type(e).__name__}: {e}", flush=True)
                traceback.print_exc()
                try:
                    await self.bot.pool.execute(
                        "UPDATE banner_state SET last_error = $1 WHERE id = 1",
                        f"{type(e).__name__}: {e}"
                    )
                except Exception:
                    pass

    @update_loop.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(30)
        state = await self._get_state()
        if state and state['circle_r'] == 0 and BASE_BANNER.exists():
            try:
                Image, _, _, _ = _import_pil()
                if Image:
                    def _det():
                        img = Image.open(str(BASE_BANNER))
                        if hasattr(img, "is_animated") and img.is_animated:
                            img.seek(0)
                        return _detect_circle(img)
                    detected = await asyncio.to_thread(_det)
                    if detected:
                        cx, cy, r = detected
                        await self.bot.pool.execute(
                            "UPDATE banner_state SET circle_x = $1, circle_y = $2, circle_r = $3 WHERE id = 1",
                            cx, cy, r
                        )
                        print(f"[Banner] Автокалибровка при старте: x={cx} y={cy} r={r}", flush=True)
            except Exception as e:
                print(f"[Banner:auto-init] {type(e).__name__}: {e}", flush=True)

    @commands.slash_command(name="баннер", description="Управление баннером сервера")
    async def banner_root(self, inter):
        pass

    @banner_root.sub_command(name="статус", description="Информация о баннере и текущем онлайне")
    async def status(self, inter):
        await inter.response.defer(ephemeral=True, with_message=True)
        if not await is_staff(inter.author.id):
            return await inter.edit_original_response(embed=_err_embed("Доступ запрещён."))

        state = await self._get_state()
        if not state:
            return await inter.edit_original_response(embed=_err_embed("Состояние не найдено. Запустите миграцию `alembic upgrade head`."))

        ok_assets, err_assets = await self._check_assets()
        guild = inter.guild
        has_banner_feature = "BANNER" in guild.features
        has_animated = "ANIMATED_BANNER" in guild.features

        count = _count_voice_online(guild)

        lines = [
            f"\u200b**・** Включён: {'🟢 да' if state['enabled'] else '🔴 нет'}",
            f"\u200b**・** Кружок: x={state['circle_x']}, y={state['circle_y']}, r={state['circle_r']}",
            f"\u200b**・** Размер шрифта: {state['font_size'] or 'авто'}",
            f"\u200b**・** Последнее значение: {state['last_count']}",
            f"\u200b**・** Текущий онлайн в войсе: **{count}**",
            f"\u200b**・** Последнее обновление: <t:{state['last_updated_at']}:R>" if state['last_updated_at'] else "\u200b**・** Ещё не обновлялось",
        ]
        if state['last_error']:
            lines.append(f"\u200b**・** Последняя ошибка: `{state['last_error'][:200]}`")
        lines.append("")
        lines.append("**Сервер:**")
        lines.append(f"\u200b**・** BANNER feature: {'✅' if has_banner_feature else '❌ нужен Boost Lvl 2+'}")
        lines.append(f"\u200b**・** ANIMATED_BANNER feature: {'✅' if has_animated else '❌ нужен Boost Lvl 3'}")
        lines.append("")
        lines.append("**Ассеты:**")
        lines.append(f"\u200b**・** GIF баннер: {'✅' if BASE_BANNER.exists() else '❌'} `{BASE_BANNER}`")
        lines.append(f"\u200b**・** Шрифт: {'✅' if FONT_FILE.exists() else '❌'} `{FONT_FILE}`")
        if not ok_assets:
            lines.append(f"\n⚠️ {err_assets}")

        await inter.edit_original_response(embed=disnake.Embed(
            title="—・Статус баннера",
            description="\n".join(lines),
            color=COLOR_NEUTRAL
        ))

    @banner_root.sub_command(name="калибровка", description="Задать позицию белого кружка вручную")
    async def calibrate(
        self,
        inter,
        x: int = commands.Param(description="X центра кружка (пиксели)"),
        y: int = commands.Param(description="Y центра кружка (пиксели)"),
        r: int = commands.Param(description="Радиус кружка (пиксели)"),
        font_size: int = commands.Param(default=0, description="Размер шрифта (0 = авто = r*1.2)")
    ):
        await inter.response.defer(ephemeral=True, with_message=True)
        if not await is_staff(inter.author.id):
            return await inter.edit_original_response(embed=_err_embed("Доступ запрещён."))

        if x <= 0 or y <= 0 or r <= 0:
            return await inter.edit_original_response(embed=_err_embed("Значения должны быть > 0."))
        if r < 5:
            return await inter.edit_original_response(embed=_err_embed("Радиус слишком маленький (минимум 5)."))

        await self.bot.pool.execute(
            "UPDATE banner_state SET circle_x = $1, circle_y = $2, circle_r = $3, font_size = $4, last_count = -1 WHERE id = 1",
            x, y, r, font_size
        )
        await inter.edit_original_response(embed=_ok_embed(
            "Калибровка сохранена",
            f"\u200b**・** x = **{x}**\n"
            f"\u200b**・** y = **{y}**\n"
            f"\u200b**・** r = **{r}**\n"
            f"\u200b**・** font_size = **{font_size if font_size else 'авто'}**\n\n"
            "Используйте `/баннер тест` чтобы посмотреть результат."
        ))

    @banner_root.sub_command(name="автокалибровка", description="Найти белый кружок на баннере автоматически")
    async def auto_calibrate(self, inter):
        await inter.response.defer(ephemeral=True, with_message=True)
        if not await is_staff(inter.author.id):
            return await inter.edit_original_response(embed=_err_embed("Доступ запрещён."))

        ok, err = await self._check_assets()
        if not ok:
            return await inter.edit_original_response(embed=_err_embed(err))

        Image, _, _, _ = _import_pil()
        if Image is None:
            return await inter.edit_original_response(embed=_err_embed("PIL не установлен. `pip install Pillow`."))

        def _detect():
            img = Image.open(str(BASE_BANNER))
            if hasattr(img, "is_animated") and img.is_animated:
                img.seek(0)
            return _detect_circle(img)

        result = await asyncio.to_thread(_detect)
        if not result:
            return await inter.edit_original_response(embed=_err_embed(
                "Не удалось найти белый кружок на баннере. Используйте `/баннер калибровка` для ручной настройки."
            ))
        cx, cy, r = result
        await self.bot.pool.execute(
            "UPDATE banner_state SET circle_x = $1, circle_y = $2, circle_r = $3, last_count = -1 WHERE id = 1",
            cx, cy, r
        )
        await inter.edit_original_response(embed=_ok_embed(
            "Автокалибровка завершена",
            f"\u200b**・** x = **{cx}**\n"
            f"\u200b**・** y = **{cy}**\n"
            f"\u200b**・** r = **{r}**\n\n"
            "Если позиция не совсем точная — поправьте через `/баннер калибровка`. "
            "Используйте `/баннер тест` чтобы посмотреть результат."
        ))

    @banner_root.sub_command(name="тест", description="Сгенерировать баннер с указанным числом и показать в ЛС")
    async def test(
        self,
        inter,
        число: int = commands.Param(default=22, description="Какое число нарисовать (по умолчанию 22)")
    ):
        await inter.response.defer(ephemeral=True, with_message=True)
        if not await is_staff(inter.author.id):
            return await inter.edit_original_response(embed=_err_embed("Доступ запрещён."))

        ok, err = await self._check_assets()
        if not ok:
            return await inter.edit_original_response(embed=_err_embed(err))

        state = await self._get_state()
        if not state or not state['circle_r']:
            return await inter.edit_original_response(embed=_err_embed(
                "Сначала задайте позицию кружка через `/баннер калибровка` или `/баннер автокалибровка`."
            ))

        try:
            def _build():
                return _render_with_fit(
                    число,
                    state['circle_x'], state['circle_y'], state['circle_r'],
                    state['font_size'] or max(20, int(state['circle_r'] * 1.2))
                )
            gif_bytes = await asyncio.to_thread(_build)
        except Exception as e:
            return await inter.edit_original_response(embed=_err_embed(f"Ошибка рендера: `{e}`"))

        file = disnake.File(io.BytesIO(gif_bytes), filename=f"banner_test_{число}.gif")
        await inter.edit_original_response(
            embed=_ok_embed(
                "Тест баннера",
                f"\u200b**・** Число: **{число}**\n"
                f"\u200b**・** Размер: **{len(gif_bytes) / 1024:.1f} KB**\n"
                f"\u200b**・** Лимит Discord: 10 MB"
            ),
            file=file
        )

    @banner_root.sub_command(name="сейчас", description="Принудительно обновить баннер прямо сейчас")
    async def update_now(self, inter):
        await inter.response.defer(ephemeral=True, with_message=True)
        if not await is_staff(inter.author.id):
            return await inter.edit_original_response(embed=_err_embed("Доступ запрещён."))

        ok, err = await self._check_assets()
        if not ok:
            return await inter.edit_original_response(embed=_err_embed(err))

        state = await self._get_state()
        if not state or not state['circle_r']:
            return await inter.edit_original_response(embed=_err_embed(
                "Сначала задайте позицию кружка через `/баннер калибровка`."
            ))

        guild = inter.guild
        if "BANNER" not in guild.features:
            return await inter.edit_original_response(embed=_err_embed(
                "У сервера нет BANNER feature (нужен Boost Level 2+)."
            ))

        count = _count_voice_online(guild)
        try:
            def _build():
                return _render_with_fit(
                    count,
                    state['circle_x'], state['circle_y'], state['circle_r'],
                    state['font_size'] or max(20, int(state['circle_r'] * 1.2))
                )
            gif_bytes = await asyncio.to_thread(_build)
            await guild.edit(banner=gif_bytes)
            await self.bot.pool.execute(
                "UPDATE banner_state SET last_count = $1, last_updated_at = $2, last_error = NULL WHERE id = 1",
                count, int(time.time())
            )
            await inter.edit_original_response(embed=_ok_embed(
                "Баннер обновлён",
                f"\u200b**・** Онлайн: **{count}**\n"
                f"\u200b**・** Размер: **{len(gif_bytes) / 1024:.1f} KB**"
            ))
        except disnake.HTTPException as e:
            await inter.edit_original_response(embed=_err_embed(f"Discord отверг загрузку: `{e}`"))
        except Exception as e:
            await inter.edit_original_response(embed=_err_embed(f"Ошибка: `{type(e).__name__}: {e}`"))

    @banner_root.sub_command(name="вкл", description="Включить автообновление баннера")
    async def enable(self, inter):
        await inter.response.defer(ephemeral=True, with_message=True)
        if not await is_staff(inter.author.id):
            return await inter.edit_original_response(embed=_err_embed("Доступ запрещён."))
        await self.bot.pool.execute("UPDATE banner_state SET enabled = TRUE WHERE id = 1")
        await inter.edit_original_response(embed=_ok_embed("Готово", "Автообновление баннера **включено**."))

    @banner_root.sub_command(name="выкл", description="Выключить автообновление баннера")
    async def disable(self, inter):
        await inter.response.defer(ephemeral=True, with_message=True)
        if not await is_staff(inter.author.id):
            return await inter.edit_original_response(embed=_err_embed("Доступ запрещён."))
        await self.bot.pool.execute("UPDATE banner_state SET enabled = FALSE WHERE id = 1")
        await inter.edit_original_response(embed=_ok_embed("Готово", "Автообновление баннера **выключено**."))


def setup(bot):
    bot.add_cog(Banner(bot))
