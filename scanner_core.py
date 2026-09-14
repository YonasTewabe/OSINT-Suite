import asyncio
import inspect
import re
import subprocess
import sys
import tempfile
import time
from typing import Callable, Optional

from email_validator import EmailNotValidError, validate_email

try:
    from user_scanner.core.helpers import (
        load_categories,
        load_modules,
        get_scan_func,
        get_site_name,
        is_loud,
    )
    from user_scanner.core.result import Result, Status
except ImportError as e:
    load_categories = None
    load_modules = None
    get_scan_func = None
    get_site_name = None
    is_loud = None
    Result = None
    Status = None

try:
    from user_scanner.core.helpers import is_valid_email
except ImportError:
    def is_valid_email(email: str) -> bool:
        return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))


HOLEHE_LINE_RE = re.compile(r"^\[([+\-x])\]\s+(.*)$")


def validate_email_target(email: str) -> tuple[Optional[str], Optional[str]]:
    """Validate email syntax and deliverability check. Returns (normalized, error)."""
    email = email.strip()
    if not email:
        return None, "Please provide an email address."
    try:
        res = validate_email(email, check_deliverability=False)
        return res.normalized, None
    except EmailNotValidError as e:
        return None, f"Invalid email format: {e}"


def validate_username_target(username: str) -> tuple[Optional[str], Optional[str]]:
    """Validate username syntax. Returns (cleaned_username, error)."""
    username = username.strip().lstrip("@")
    if not username:
        return None, "Please provide a username."
    if len(username) < 2:
        return None, "Username must be at least 2 characters long."
    if re.search(r"\s", username):
        return None, "Username cannot contain spaces."
    return username, None


def run_holehe_found_only(email: str, timeout: int = 120) -> tuple[list[str], Optional[str]]:
    """
    Run Holehe CLI with --only-used and return ONLY confirmed found platforms (+).
    Returns (found_sites_list, error_msg).
    """
    py_code = "import sys; from holehe.core import main; sys.argv = ['holehe'] + sys.argv[1:]; main()"
    cmd = [sys.executable, "-c", py_code, email, "-C", "--only-used"]
    found_sites: list[str] = []

    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, cwd=tmpdir
            )
    except subprocess.TimeoutExpired:
        return [], "Holehe lookup timed out."
    except Exception as e:
        return [], f"Failed to execute Holehe: {e}"

    for line in proc.stdout.splitlines():
        line = line.strip()
        if "Email used" in line and "Email not used" in line:
            continue
        match = HOLEHE_LINE_RE.match(line)
        if match:
            status, site = match.groups()
            site = site.strip()
            if "[" in site or "]" in site:
                continue
            # Strictly filter to confirmed positive results
            if status == "+":
                found_sites.append(site)

    error = None
    if proc.returncode != 0 and not found_sites:
        error = proc.stderr.strip() or "Holehe scan returned no results or failed."

    # Sort found sites alphabetically
    found_sites.sort(key=str.lower)
    return found_sites, error


async def _us_scan_stream(target: str, is_email: bool, no_nsfw: bool = True, allow_loud: bool = False):
    """Internal async generator for user-scanner."""
    if not load_categories:
        raise RuntimeError("user_scanner is not installed or available.")

    categories = load_categories(is_email=is_email, no_nsfw=no_nsfw)
    all_modules = []
    for cat_name, cat_path in categories.items():
        for module in load_modules(cat_path):
            all_modules.append((cat_name.capitalize(), module))

    sem = asyncio.Semaphore(40)
    queue: asyncio.Queue = asyncio.Queue()

    async def worker(cat_name: str, module):
        async with sem:
            site_name = get_site_name(module)
            func = get_scan_func(module)
            params = {
                "site_name": site_name.capitalize(),
                "username": target,
                "category": cat_name,
                "is_email": is_email,
            }
            if not func:
                await queue.put(Result.error(f"{site_name} has no validate_ function", **params))
                return
            if not allow_loud and is_loud(site_name, is_email=is_email):
                await queue.put(Result.skipped().update(**params))
                return
            try:
                if inspect.iscoroutinefunction(func):
                    result = await func(target)
                else:
                    result = await asyncio.to_thread(func, target)
            except Exception as e:
                result = Result.error(e)
            result.update(**params)
            await queue.put(result)

    tasks = [asyncio.create_task(worker(cat, mod)) for cat, mod in all_modules]
    total = len(tasks)
    done = 0

    while done < total:
        result = await queue.get()
        done += 1
        yield result, done, total

    await asyncio.gather(*tasks, return_exceptions=True)


async def run_us_found_only_async(
    target: str,
    is_email: bool,
    allow_loud: bool = False,
    progress_callback: Optional[Callable[[int, int, int], None]] = None,
) -> tuple[list[dict], Optional[str]]:
    """
    Async run of user-scanner returning ONLY confirmed found results.
    Returns (found_items, error).
    found_items contains dicts: {"site_name": str, "category": str, "url": str}
    """
    found_items = []
    try:
        last_callback_time = 0.0
        async for result, done, total in _us_scan_stream(target, is_email, no_nsfw=False, allow_loud=allow_loud):
            # Check if found (status_code 0 is registered/found)
            if getattr(result, "status", None) and result.status.value == 0:
                found_items.append({
                    "site_name": result.site_name or "",
                    "category": result.category or "",
                    "url": result.url or "",
                })

            if progress_callback:
                now = time.time()
                # Throttle progress callbacks to at most once every 1.5 seconds
                if (now - last_callback_time >= 1.5) or done == total:
                    last_callback_time = now
                    try:
                        res = progress_callback(done, total, len(found_items))
                        if inspect.isawaitable(res):
                            await res
                    except Exception:
                        pass

    except Exception as e:
        return found_items, str(e)

    found_items.sort(key=lambda x: x["site_name"].lower())
    return found_items, None


def run_us_found_only_sync(
    target: str,
    is_email: bool,
    allow_loud: bool = False,
    progress_callback: Optional[Callable[[int, int, int], None]] = None,
) -> tuple[list[dict], Optional[str]]:
    """Synchronous wrapper around run_us_found_only_async."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            run_us_found_only_async(target, is_email, allow_loud, progress_callback)
        )
    finally:
        loop.close()


def generate_export_text(target: str, scan_name: str, found_items: list) -> str:
    """Generate plain text export for found items."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    lines = [
        "OSINT Suite - Found Accounts Report",
        f"Target: {target}",
        f"Scan Engine: {scan_name}",
        f"Generated At: {timestamp}",
        f"Total Found: {len(found_items)}",
        "-" * 50,
        "",
    ]
    if not found_items:
        lines.append("No registered accounts were found on the scanned platforms.")
    else:
        for item in found_items:
            if isinstance(item, str):
                lines.append(f"• {item}")
            else:
                cat = f"[{item.get('category')}] " if item.get("category") else ""
                url = f" - {item.get('url')}" if item.get("url") else ""
                lines.append(f"• {cat}{item.get('site_name', '')}{url}")

    return "\n".join(lines)


def format_telegram_report(
    target: str,
    scan_name: str,
    found_items: list,
    elapsed_seconds: Optional[float] = None,
) -> tuple[str, Optional[str]]:
    """
    Format Telegram chat response showing ONLY found accounts.
    Returns (telegram_message_text, optional_overflow_txt_content).
    """
    count = len(found_items)
    duration_str = f" in {elapsed_seconds:.1f}s" if elapsed_seconds is not None else ""

    header = (
        f"🎯 <b>Scan Results for</b> <code>{target}</code>\n"
        f"⚡ <b>Engine:</b> {scan_name}{duration_str}\n"
        f"✅ <b>Found Accounts:</b> {count}\n\n"
    )

    if count == 0:
        msg = (
            f"🎯 <b>Scan Results for</b> <code>{target}</code>\n"
            f"⚡ <b>Engine:</b> {scan_name}{duration_str}\n\n"
            f"ℹ️ <i>No confirmed accounts were found on any scanned platforms.</i>"
        )
        return msg, None

    body_lines = []
    for item in found_items:
        if isinstance(item, str):
            body_lines.append(f"• <b>{item}</b>")
        else:
            site = item.get("site_name", "")
            cat = f"<i>{item.get('category')}</i> · " if item.get("category") else ""
            url = item.get("url")
            if url:
                body_lines.append(f"• {cat}<a href=\"{url}\">{site}</a>")
            else:
                body_lines.append(f"• {cat}<b>{site}</b>")

    full_message = header + "\n".join(body_lines)

    # Telegram message limit is 4096. Keep safe margin at 3800.
    if len(full_message) <= 3800:
        return full_message, None

    # Exceeds message length -> prepare summary + full text export
    truncated_lines = body_lines[:25]
    summary_msg = (
        header
        + "\n".join(truncated_lines)
        + f"\n\n<i>...and {count - len(truncated_lines)} more platforms.</i>\n"
        + "📄 <b>Full report attached below.</b>"
    )
    full_export = generate_export_text(target, scan_name, found_items)
    return summary_msg, full_export
