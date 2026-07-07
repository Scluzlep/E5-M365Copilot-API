"""Flatten an OpenAI ``messages`` array into a single Copilot prompt.

Copilot's protocol has no role/system channel — it takes one prompt string per
turn — so we collapse the whole conversation into one piece of text.
"""

from typing import Any, List, Optional, Union
import base64
import uuid

from .schemas import ChatMessage

import mimetypes

ALLOWED_MIMES = {
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-word.document.macroenabled.12',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.ms-powerpoint',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'text/plain',
    'text/csv',
    'application/pdf',
    'application/rtf',
    'application/vnd.microsoft.loop',
    'application/vnd.microsoft.fluid',
    'text/tab-separated-values',
    'text/html',
    'text/markdown',
    'application/xml',
    'application/yaml',
    'application/x-sh',
    'text/css',
    'application/json',
    'text/x-java-source',
    'application/javascript',
    'text/jscript',
    'application/sql',
    'application/vnd.ms-excel.sheet.macroenabled.12',
    'application/vnd.ms-powerpoint.slideshow.macroenabled.12',
    'application/vnd.microsoft.page',
    'image/jpeg',
    'image/png',
    'image/bmp',
    'image/jfif',
    'image/gif',
    'image/pjpeg',
    'image/pjp',
    'image/webp'
}

ALLOWED_EXTS = {
    '.doc', '.docx', '.docm', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.csv', '.pdf', '.rtf',
    '.loop', '.fluid', '.tsv', '.html', '.md', '.xml', '.c', '.yaml', '.php', '.sh', '.dart',
    '.css', '.lua', '.config', '.utf8', '.htm', '.cpp', '.yml', '.log', '.h', '.bash', '.ini',
    '.json', '.java', '.pl', '.rs', '.js', '.py', '.tsx', '.cs', '.jsx', '.sql', '.xlsm',
    '.ppsm', '.page', '.jpg', '.jpeg', '.png', '.bmp', '.jfif', '.gif', '.pjpeg', '.pjp', '.webp'
}

MIME_TO_EXT = {
    'application/msword': '.doc',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
    'application/vnd.ms-word.document.macroenabled.12': '.docm',
    'application/vnd.ms-excel': '.xls',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
    'application/vnd.ms-powerpoint': '.ppt',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': '.pptx',
    'text/plain': '.txt',
    'text/csv': '.csv',
    'application/pdf': '.pdf',
    'application/rtf': '.rtf',
    'application/vnd.microsoft.loop': '.loop',
    'application/vnd.microsoft.fluid': '.fluid',
    'text/tab-separated-values': '.tsv',
    'text/html': '.html',
    'text/markdown': '.md',
    'application/xml': '.xml',
    'application/yaml': '.yaml',
    'application/x-sh': '.sh',
    'text/css': '.css',
    'application/json': '.json',
    'text/x-java-source': '.java',
    'application/javascript': '.js',
    'text/jscript': '.js',
    'application/sql': '.sql',
    'application/vnd.ms-excel.sheet.macroenabled.12': '.xlsm',
    'application/vnd.ms-powerpoint.slideshow.macroenabled.12': '.ppsm',
    'application/vnd.microsoft.page': '.page',
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/bmp': '.bmp',
    'image/jfif': '.jfif',
    'image/gif': '.gif',
    'image/pjpeg': '.pjpeg',
    'image/pjp': '.pjp',
    'image/webp': '.webp',
}

def get_ext_for_mime(mime: str) -> str:
    mime_lower = mime.lower()
    if mime_lower in MIME_TO_EXT:
        return MIME_TO_EXT[mime_lower]
    ext = mimetypes.guess_extension(mime_lower)
    return ext if ext else '.txt'

def extract_files(messages: List[ChatMessage], last_turn_only: bool = False) -> List[dict]:
    if last_turn_only and messages:
        target_messages = []
        for m in reversed(messages):
            if m.role == "user":
                target_messages.append(m)
            else:
                break
        target_messages.reverse()
    else:
        target_messages = messages

    files = []
    for m in target_messages:
        if not isinstance(m.content, list):
            continue
        for part in m.content:
            if not isinstance(part, dict):
                continue
            
            b64_data = None
            mime_type = ""
            file_name = None
            
            if part.get("type") == "image_url":
                url_obj = part.get("image_url", {})
                url = url_obj.get("url", "")
                if url.startswith("data:"):
                    try:
                        header, b64_data = url.split(",", 1)
                        mime_type = header.split(";")[0].replace("data:", "")
                    except ValueError:
                        continue
            elif part.get("type") in ("image", "document", "file"):
                source = part.get("source", {})
                if source.get("type") == "base64":
                    b64_data = source.get("data")
                    mime_type = source.get("media_type", "")
            
            if b64_data:
                try:
                    data_bytes = base64.b64decode(b64_data)
                except Exception:
                    continue
                
                mime_lower = mime_type.lower()
                is_valid_mime = mime_lower in ALLOWED_MIMES
                
                ext = ""
                if "name" in part:
                    file_name = part["name"]
                    if "." in file_name:
                        ext = "." + file_name.split(".")[-1].lower()
                        
                is_valid_ext = ext in ALLOWED_EXTS if ext else False
                
                if not (is_valid_mime or is_valid_ext):
                    try:
                        text_content = data_bytes.decode("utf-8")
                        mime_type = "text/plain"
                        if file_name:
                            if not file_name.lower().endswith(".txt"):
                                file_name = f"{file_name}.txt"
                        else:
                            file_name = f"upload_{uuid.uuid4().hex[:8]}.txt"
                    except UnicodeDecodeError:
                        continue
                
                if not file_name:
                    ext = get_ext_for_mime(mime_type)
                    file_name = f"upload_{uuid.uuid4().hex[:8]}{ext}"
                    
                files.append({
                    "data": data_bytes,
                    "mime_type": mime_type,
                    "file_name": file_name
                })
                
    if len(files) > 3:
        raise ValueError("At most 3 files can be uploaded per turn.")
                
    return files

def content_text(content: Optional[Union[str, List[Any]]]) -> str:
    """Extract plain text from a message's content (string or content-parts)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for part in content:
        if isinstance(part, dict):
            if part.get("type") == "text":
                parts.append(part.get("text", ""))
        else:
            parts.append(str(part))
    return "\n".join(p for p in parts if p)


def messages_to_prompt(messages: List[ChatMessage]) -> str:
    """Flatten an OpenAI ``messages`` array into a single Copilot prompt."""
    system = "\n\n".join(
        content_text(m.content) for m in messages if m.role == "system" and m.content
    )
    convo = [m for m in messages if m.role != "system"]

    if len(convo) == 1 and convo[0].role == "user":
        body = content_text(convo[0].content)  # simple single-turn request
    else:
        lines = []
        for m in convo:
            label = "User" if m.role == "user" else "Assistant"
            lines.append(f"{label}: {content_text(m.content)}")
        lines.append("Assistant:")  # cue Copilot to continue
        body = "\n".join(lines)

    if system and body:
        return f"{system}\n\n{body}"
    return system or body
