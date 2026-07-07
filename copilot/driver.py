"""Pure-HTTP Copilot driver for Microsoft 365 E5 Substrate.

Speaks Microsoft 365 E5 Copilot chat protocol directly over a
``curl_cffi`` session. This is the low-level engine; most callers should use
:class:`copilot.client.CopilotClient`. See :mod:`copilot.browser` for interactive login.
"""

import base64
import json
import time
import uuid
from select import select
from typing import Any, Dict, List, Optional, Union
from urllib.parse import quote

from curl_cffi.const import CurlECode, CurlInfo
from curl_cffi.curl import CurlError
from curl_cffi.requests import Session, CurlWsFlag

# curl_cffi's WebSocket.recv() loops on CURLE_AGAIN forever (select() then retry)
# and never returns on an idle socket, so we drive the fragment loop ourselves to
# honour a deadline. CURL_SOCKET_BAD is libcurl's "no active socket" sentinel.
_CURL_SOCKET_BAD = -1

from .challenges import solve_copilot_challenge, solve_hashcash
from .models import AbstractProvider, Conversation, ImageResponse, ImageType
from .protocol import CHAT_WEBSOCKET_URL, CONSENTS_FRAME, SET_OPTIONS_FRAME
from .useragent import CHROME_CLIENT_HINTS, CHROME_UA, IMPERSONATE_TARGET, US_ACCEPT_LANGUAGE
from .utils import drain_json, is_accepted_format, raise_for_status, to_bytes, mask_token
from .agent_registry import get_agent_config


class Copilot(AbstractProvider):
    label = "Microsoft Copilot"
    url = "https://m365.cloud.microsoft"
    working = True
    supports_stream = True
    default_model = "Copilot"
    needs_auth = True  # E5 commercial accounts require authentication
    websocket_url = CHAT_WEBSOCKET_URL
    conversation_url = f"{url}/c/api/conversations"

    def create_completion(
            self,
            prompt: str,
            stream: bool = False,
            proxy: str = None,
            timeout: int = 900,
            image: ImageType = None,
            conversation: Optional[Conversation] = None,
            conversation_id: str = None,
            return_conversation: bool = False,
            cookies: Dict[str, str] = None,
            access_token: str = None,
            identity_type: str = None,
            model: Optional[str] = None,
            gpt_id: Optional[str] = None,
            options_sets: Optional[List[str]] = None,
            mode: Optional[str] = None,
            plugins: Optional[List[Any]] = None,
            **kwargs
        ):
        """Stream a Copilot reply to ``prompt``.

        Runs Copilot's own chat protocol over an HTTP/2
        ``curl_cffi`` session: ``POST /c/api/conversations`` then a chat
        WebSocket (``send`` -> proof-of-work ``challenge`` -> ``appendText``* ->
        ``done``). The challenge is solved in-process (see
        :mod:`copilot.challenges`); no browser is required.

        ``prompt`` is the user message sent straight to the chat socket (the
        protocol has no separate system/role channel). Pass ``cookies`` and/or
        ``access_token`` (e.g. exported from a signed-in browser session) to run
        as an authenticated E5 Substrate user.

        Conversation targeting (first match wins):
          * ``conversation`` — reuse an existing :class:`Conversation` object;
          * ``conversation_id`` — resume a conversation by its id string (no
            create call), e.g. one saved from a previous run;
          * neither — create a fresh conversation. With ``return_conversation``
            the new :class:`Conversation` is yielded first.
        """
        # Resolve agent/model routing configuration for Enterprise / E5 Copilot
        agent_cfg = get_agent_config(model)
        resolved_mode = mode or agent_cfg.get("mode", "Magic")
        resolved_gpt_id = gpt_id or agent_cfg.get("gptId")
        resolved_options_sets = (options_sets or []) + (agent_cfg.get("optionsSets") or [])
        resolved_options_sets = list(dict.fromkeys(resolved_options_sets)) if resolved_options_sets else None

        resolved_plugins = []
        if plugins:
            for p in plugins:
                if isinstance(p, dict):
                    resolved_plugins.append(p)
                elif isinstance(p, str):
                    p_lower = p.lower()
                    if p_lower in ("web_search", "bingwebsearch", "bing"):
                        resolved_plugins.append({"Id": "BingWebSearch", "Source": "BuiltIn"})
                    elif p_lower == "acrobat":
                        resolved_plugins.append({"Id": "P_95ececa2-8770-8e92-fd40-8983e3c2adab.acrobatAgent", "Source": "Tenant"})
                    elif p_lower == "code_interpreter":
                        if resolved_options_sets is None:
                            resolved_options_sets = []
                        if "cwc_code_interpreter" not in resolved_options_sets:
                            resolved_options_sets.append("cwc_code_interpreter")
                    else:
                        resolved_plugins.append({"Id": p, "Source": "Tenant"})

        # Resolve auth: explicit args win, else fall back to the conversation's.
        if cookies is None and conversation is not None:
            cookies = conversation.cookies
        if access_token is None and conversation is not None:
            access_token = conversation.access_token

        with Session(
            timeout=timeout,
            proxy=proxy,
            # Pin the TLS/HTTP2 fingerprint, then override the UA + client hints so
            # the wire presentation is a fixed Windows Chrome.
            impersonate=IMPERSONATE_TARGET,
            headers={"User-Agent": CHROME_UA, "Accept-Language": US_ACCEPT_LANGUAGE, **CHROME_CLIENT_HINTS},
            cookies=cookies,
        ) as session:
            # Establish session cookies.
            session.get(f"{self.url}/")

            if conversation is not None and conversation.conversation_id:
                conversation_id = conversation.conversation_id
            elif conversation_id is not None:
                pass  # resume an existing conversation by id; skip create
            else:
                # E5 Business Chat does not use a REST endpoint to create conversations;
                # it accepts any client-generated UUID as the conversationId.
                if "m365.cloud.microsoft" in self.url:
                    conversation_id = str(uuid.uuid4())
                else:
                    response = session.post(self.conversation_url)
                    raise_for_status(response)
                    conversation_id = response.json().get("id")
                
                if return_conversation:
                    yield Conversation(conversation_id, session.cookies.jar)

            e5_attachments = kwargs.get("e5_attachments") or []
            # Backwards compatibility for single image
            if image is not None:
                e5_attachments.append({
                    "data": to_bytes(image),
                    "mime_type": is_accepted_format(to_bytes(image)),
                    "file_name": f"image_{uuid.uuid4().hex[:8]}.jpg"
                })

            if len(e5_attachments) > 3:
                raise ValueError("At most 3 files can be uploaded per turn.")

            images = []
            message_annotations = []
            
            if e5_attachments:
                if "m365.cloud.microsoft" in self.url:
                    for att in e5_attachments:
                        data = att["data"]
                        mime = att["mime_type"]
                        fname = att["file_name"]
                        
                        scenario = "UploadImage" if mime.startswith("image/") else "CopilotAttachment"
                        
                        def _try_upload(scen):
                            boundary = '----WebKitFormBoundary' + uuid.uuid4().hex
                            data_b64 = f"data:{mime};base64,{base64.b64encode(data).decode('utf-8')}"
                            body = (
                                f"--{boundary}\r\n"
                                f'Content-Disposition: form-data; name="scenario"\r\n\r\n'
                                f'{scen}\r\n'
                                f'--{boundary}\r\n'
                                f'Content-Disposition: form-data; name="conversationId"\r\n\r\n'
                                f'{conversation_id}\r\n'
                                f'--{boundary}\r\n'
                                f'Content-Disposition: form-data; name="FileBase64"\r\n\r\n'
                                f'{data_b64}\r\n'
                                f'--{boundary}--\r\n'
                            ).encode('utf-8')
                            
                            headers = {
                                "content-type": f"multipart/form-data; boundary={boundary}",
                            }
                            if access_token:
                                headers["authorization"] = f"Bearer {access_token}"
                                
                            resp = session.post(
                                "https://substrate.office.com/m365Copilot/UploadFile",
                                headers=headers,
                                data=body,
                            )
                            raise_for_status(resp)
                            res_json = resp.json()
                            fid = res_json.get("id")
                            furl = res_json.get("url") or res_json.get("FileUrl")
                            if not fid or not furl:
                                raise ValueError("Missing id or url")
                            return fid, furl

                        try:
                            file_id, file_url = _try_upload(scenario)
                        except Exception:
                            if scenario != "UploadImage":
                                try:
                                    file_id, file_url = _try_upload("UploadImage")
                                except Exception:
                                    file_id, file_url = None, None
                            else:
                                file_id, file_url = None, None
                                
                        if not file_id or not file_url:
                            raise RuntimeError(f"Failed to upload attachment {fname}: missing id/url in Substrate response.")
                            
                        # If it's an image, Copilot supports it in the content array.
                        # For other files, we attach them as LocalFile in messageAnnotations.
                        if mime.startswith("image/"):
                            images.append({"type": "image", "id": file_id, "url": file_url})
                        else:
                            message_annotations.append({
                                "id": file_id,
                                "text": fname,
                                "url": file_url,
                                "messageAnnotationType": "LocalFile"
                            })
                else:
                    # Non-E5 fallback (only supports single image via /attachments)
                    for att in e5_attachments:
                        if att["mime_type"].startswith("image/"):
                            if images:
                                print(f"[Driver] Warning: Non-E5 endpoint only supports a single image. Dropping additional attachment: {att['file_name']}")
                                continue
                            response = session.post(
                                f"{self.url}/c/api/attachments",
                                headers={"content-type": att["mime_type"]},
                                data=att["data"],
                            )
                            raise_for_status(response)
                            images.append({"type": "image", "id": str(uuid.uuid4()), "url": response.json().get("url")})
                        else:
                            print(f"[Driver] Warning: Non-E5 endpoint does not support non-image attachment: {att['file_name']}")

            send_payload = {
                "event": "send",
                "conversationId": conversation_id,
                "content": [*images, {"type": "text", "text": prompt}],
                "mode": resolved_mode,
                "context": {},
            }
            if message_annotations:
                send_payload["messageAnnotations"] = message_annotations
            if resolved_gpt_id:
                send_payload["gptId"] = resolved_gpt_id
            if resolved_options_sets:
                send_payload["optionsSets"] = resolved_options_sets
            if resolved_plugins:
                send_payload["plugins"] = resolved_plugins

            send_frame = json.dumps(send_payload).encode()

            # -----------------------------------------------------------------
            # Construct the WebSocket connection URL
            # -----------------------------------------------------------------
            if "m365.cloud.microsoft" in self.url:
                # Decode oid and tid from access_token JWT
                oid = None
                tid = None
                if access_token:
                    import base64
                    try:
                        parts = access_token.split(".")
                        if len(parts) >= 2:
                            payload_b64 = parts[1]
                            payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
                            payload_data = json.loads(base64.urlsafe_b64decode(payload_b64.encode()).decode("utf-8", "ignore"))
                            oid = payload_data.get("oid")
                            tid = payload_data.get("tid")
                    except Exception as e:
                        print(f"[Driver] Failed to decode JWT for oid/tid: {mask_token(e)}")
                
                # Fallbacks if decoding fails
                if not oid or not tid:
                    raise RuntimeError(
                        "Failed to decode oid/tid from access token JWT. "
                        "The token may be malformed or expired. Re-login with `python -m copilot login`."
                    )
                    
                session_id = str(uuid.uuid4())
                client_req_id = str(uuid.uuid4())
                
                websocket_url = (
                    f"wss://substrate.svc.cloud.microsoft/m365Copilot/Chathub/{oid}@{tid}"
                    f"?chatsessionid={client_req_id}"
                    f"&XRoutingParameterSessionKey={client_req_id}"
                    f"&clientrequestid={client_req_id}"
                    f"&X-SessionId={session_id}"
                    f"&ConversationId={conversation_id}"
                )
                if access_token:
                    websocket_url += f"&access_token={quote(access_token)}"
                
                # Append standard E5 features / variants
                websocket_url += (
                    "&variants=EnableMcpServerWidgets,feature.EnableMcpServerWidgets,"
                    "feature.EnableImageGenInsufficientTokensThrottled,feature.EnableImageGenSystemCapacityThrottled,"
                    "feature.EnableLuForChatCIQ,feature.enableChatCIQPlugin,EnableRequestPlugins,"
                    "feature.EnableSensitivityLabels,EnableUnsupportedUrlDetector,feature.IsCustomEngineCopilotEnabled,"
                    "feature.bizchatfluxv3,feature.enablechatpages,feature.enableCodeCanvas,"
                    "feature.turnOnWorkTabRecommendation,feature.turnOnDARecommendation,"
                    "feature.IsStreamingModeInChatRequestEnabled,IncludeSourceAttributionsConcise,"
                    "SkipPublishEmptyMessage,feature.EnableDeduplicatingSourceAttributions,"
                    "feature.IsCitationsReferencesOutputEnabled,feature.enableDeltaStreamingForReferences,"
                    "feature.enableIncludeReferencesInDeltaResponse,feature.enablereferencesforagents,"
                    "Enable3PActionProgressMessages,feature.enableClientWebRtc,"
                    "feature.EnableMeetingRecapOfSeriesMeetingWithCiq,feature.EnableReferencesListCompleteSignal,"
                    "feature.StorageMessageSplitDisabled,feature.EnableCuaTakeControlApi,SingletonEnvOn,"
                    "agt_bizchat_enablePagesCitations,agt_module_canvasSetup_enablePagesCitations,"
                    "agt_bizchat_enablePagesCitationsForMultiturn,agt_module_canvasSetup_enablePagesCitationsForMultiturn,"
                    "cdxenablefccinmainline,EnableComposeWidget,feature.cwcallowedos,feature.EnableMergingPureDeltas,"
                    "feature.disabledisallowedmsgs,feature.enableCitationsForSynthesisData,feature.EnableConversationShareApis,"
                    "feature.enableGenerateGraphicArtOptionsSet,cdximagen,feature.EnableUpdatedUXForConfirmationDialog,"
                    "feature.EnableContentApiandDocTypeHtmlInRichAnswers,"
                    "cdxgrounding_api_v2_rich_web_answers_reference_bottom_force,cdxenablerenderforisocomp,"
                    "feature.EnableClientFileURLSupportForOfficeWebPaidCopilot,feature.EnableDesignEditorImageGrounding,"
                    "feature.EnableDesignerEditor,feature.EnableSkipRehydrationForSpeCIdImages,feature.EnablePersonalization,"
                    "rich_responses,feature.EnableBase64DataInMessageAnnotations,feature.EnableSkipEmittingMessageOnFlush,"
                    "feature.EnableRemoveEmptySourceAttributions,feature.EnableRemoveStreamingMode,"
                    "feature.OfficeWebToHelix,feature.OfficeDesktopToHelix,feature.M365TeamsHubToHelix,"
                    "feature.OwaHubToHelix,feature.MonarchHubToHelix,feature.Win32OutlookHubToHelix,"
                    "feature.MacOutlookHubToHelix,Agt_bizchat_enableGpt5ForHelix"
                    "&source=%22officeweb%22"
                    "&product=Office"
                    "&agentHost=Bizchat.FullScreen"
                    "&licenseType=Starter"
                    "&isEdu=false"
                    "&agent=web"
                    "&scenario=OfficeWebIncludedCopilot"
                )
            else:
                websocket_url = f"{self.websocket_url}&clientSessionId={uuid.uuid4()}"
                if access_token:
                    websocket_url = f"{websocket_url}&accessToken={quote(access_token)}"
                    if identity_type:
                        websocket_url = f"{websocket_url}&X-UserIdentityType={quote(identity_type)}"

            print("[Driver] Connecting to WebSocket...")

            wss = session.ws_connect(websocket_url)
            print("[Driver] WebSocket connected successfully.")
            try:
                if "m365.cloud.microsoft" in self.url:
                    # E5 Business Chat uses ASP.NET Core SignalR protocol
                    wss.send('{"protocol":"json","version":1}\x1e'.encode("utf-8"), CurlWsFlag.TEXT)
                    wss.send('{"type":6}\x1e'.encode("utf-8"), CurlWsFlag.TEXT)
                    
                    # Build E5 SignalR StreamInvocation frame
                    e5_payload = {
                        "arguments": [
                            {
                                "source": "officeweb",
                                "clientCorrelationId": str(uuid.uuid4()),
                                "sessionId": str(uuid.uuid4()),
                                "optionsSets": resolved_options_sets or [
                                    "search_result_progress_messages_with_search_queries",
                                    "update_textdoc_response_after_streaming",
                                    "cwc_flux_image",
                                    "cwc_code_interpreter",
                                    "cwc_flux_v3",
                                    "rich_responses",
                                    "pages_citations",
                                    "pages_citations_multiturn",
                                ],
                                "streamingMode": "ConciseWithPadding",
                                "allowedMessageTypes": [
                                    "Chat", "Suggestion", "Progress", "GeneratedCode",
                                    "RenderCardRequest", "GenerateContentQuery",
                                    "GenerateGraphicArt", "SearchQuery", "ConfirmationCard",
                                    "AuthError", "DeveloperLogs", "TriggerPlugin",
                                    "HintInvocation", "MemoryUpdate", "EndOfRequest",
                                    "TriggerConfirmation", "ReferencesListComplete",
                                ],
                                "traceId": str(uuid.uuid4()),
                                "isStartOfSession": True,
                                "clientInfo": {
                                    "clientPlatform": "mcmcopilot-web",
                                    "clientAppName": "Office",
                                    "clientEntrypoint": "mcmcopilot-officeweb",
                                    "clientSessionId": str(uuid.uuid4()),
                                    "ProductCategory": "Chat",
                                    "clientAppType": "Web",
                                    "productEntryPoint": "ChatPanel",
                                    "deviceOS": "Windows",
                                    "deviceType": "Desktop",
                                    "clientPlatformVersion": "10",
                                },
                                "message": {
                                    "author": "user",
                                    "inputMethod": "Keyboard",
                                    "text": prompt,
                                    "requestId": str(uuid.uuid4()),
                                    "locale": "zh-cn",
                                    "messageType": "Chat",
                                    "experienceType": "Default",
                                    "messageAnnotations": [
                                        {
                                            "id": img.get("id", str(uuid.uuid4())),
                                            "messageAnnotationMetadata": {
                                                "@type": "File",
                                                "annotationType": "File",
                                                "fileType": "jpg",
                                                "fileName": "image.jpg",
                                                "url": img.get("url", "")
                                            },
                                            "messageAnnotationType": "ImageFile"
                                        } for img in images
                                    ] if images else [],
                                },
                                "plugins": resolved_plugins or [{"Id": "BingWebSearch", "Source": "BuiltIn"}],
                                "isSbsSupported": True,
                                "tone": resolved_mode or "Gpt_5_5_Reasoning",
                                "renderReferencesBehindEOS": True,
                                "disconnectBehavior": "continue",
                            }
                        ],
                        "invocationId": "0",
                        "target": "chat",
                        "type": 4,
                    }
                    if resolved_gpt_id:
                        e5_payload["arguments"][0]["threadLevelGptId"] = {"gptId": resolved_gpt_id}
                    wss.send((json.dumps(e5_payload) + "\x1e").encode("utf-8"), CurlWsFlag.TEXT)
                    yield from self._read_e5_stream(wss, timeout)
                else:
                    # Initialise the session before sending: setOptions then
                    # reportLocalConsents. A `send` issued first is rejected with
                    # `invalid-event` (see the handshake constants above).
                    options_frame = SET_OPTIONS_FRAME.copy()
                    if resolved_options_sets:
                        options_frame["optionsSets"] = resolved_options_sets
                    if resolved_plugins:
                        options_frame["plugins"] = resolved_plugins
                    wss.send(json.dumps(options_frame).encode(), CurlWsFlag.TEXT)
                    wss.send(json.dumps(CONSENTS_FRAME).encode(), CurlWsFlag.TEXT)
                    wss.send(send_frame, CurlWsFlag.TEXT)
                    yield from self._read_stream(wss, send_frame, timeout)
            finally:
                try:
                    wss.close()
                except Exception:
                    pass

    def _read_stream(self, wss, send_frame: bytes, timeout: int, idle_timeout: int = 60):
        """Consume chat-socket frames, solving challenges, yielding text/images.

        ``idle_timeout`` bounds how long we wait for the *next* frame: the chat
        backend normally answers within a second, so prolonged silence means a
        stalled socket (or a challenge we failed to answer) — we raise rather
        than block for the full ``timeout``.
        """
        buffer = b""
        is_started = False
        answered = False
        image_prompt = None
        last_msg = None

        overall_deadline = time.time() + timeout
        while True:
            idle_deadline = time.time() + idle_timeout
            try:
                chunk = self._recv_frame(wss, min(overall_deadline, idle_deadline))
            except Exception:
                break  # socket closed/errored -> end of stream
            if chunk is None:  # deadline passed with no frame
                if time.time() >= overall_deadline:
                    raise TimeoutError(f"Copilot stream exceeded {timeout}s")
                raise TimeoutError(
                    f"Copilot chat socket went silent for {idle_timeout}s; "
                    f"last frame was {last_msg!r}."
                )

            buffer += chunk if isinstance(chunk, (bytes, bytearray)) else chunk.encode()
            messages, buffer = drain_json(buffer)
            for msg in messages:
                last_msg = msg
                event = msg.get("event")
                if event == "challenge":
                    method = msg.get("method")
                    if answered:
                        continue  # already answered the PoW for this turn; ignore echo
                    token = self._solve_challenge(msg)
                    if token is None:
                        raise RuntimeError(
                            f"Unsolvable Copilot challenge (method={method!r}). "
                            "Please re-login or check session credentials."
                        )
                    wss.send(json.dumps({
                        "event": "challengeResponse",
                        "token": token,
                        "method": msg.get("method"),
                        "id": msg.get("id"),
                    }).encode(), CurlWsFlag.TEXT)
                    answered = True
                    # The client re-sends the held message after a challenge.
                    wss.send(send_frame, CurlWsFlag.TEXT)
                elif event == "appendText":
                    is_started = True
                    yield msg.get("text")
                elif event == "generatingImage":
                    image_prompt = msg.get("prompt")
                elif event == "imageGenerated":
                    yield ImageResponse(msg.get("url"), image_prompt, {"preview": msg.get("thumbnailUrl")})
                elif event == "done":
                    return
                elif event == "error":
                    code = msg.get("errorCode") or msg
                    if code == "chat-service-unavailable":
                        raise RuntimeError(
                            "Copilot error: chat-service-unavailable. The chat backend is "
                            "typically geo-restricted; if you are outside a supported region, "
                            "retry via a proxy in a supported region, e.g. "
                            "create_completion(..., proxy='http://user:pass@host:port')."
                        )
                    raise RuntimeError(f"Copilot error: {code}")

        if not is_started:
            raise RuntimeError(f"Invalid response: {last_msg}")

    def _read_e5_stream(self, wss, timeout: int, idle_timeout: int = 60):
        """Consume E5 SignalR chat-socket frames, yielding streamed text chunks."""
        buffer = ""
        is_started = False
        last_msg = None
        seen_text = ""
        seen_thoughts = set()
        overall_deadline = time.time() + timeout

        while True:
            idle_deadline = time.time() + idle_timeout
            try:
                chunk = self._recv_frame(wss, min(overall_deadline, idle_deadline))
            except Exception:
                break
            if chunk is None:
                if time.time() >= overall_deadline:
                    raise TimeoutError(f"Copilot stream exceeded {timeout}s")
                raise TimeoutError(f"Copilot chat socket went silent for {idle_timeout}s.")

            text_chunk = chunk if isinstance(chunk, str) else chunk.decode("utf-8", "ignore")
            buffer += text_chunk
            parts = buffer.split("\x1e")
            buffer = parts[-1]
            for part in parts[:-1]:
                if not part.strip():
                    continue
                try:
                    msg = json.loads(part)
                except Exception:
                    continue
                last_msg = msg
                msg_type = msg.get("type")
                if msg_type == 6:
                    try:
                        wss.send('{"type":6}\x1e'.encode("utf-8"), CurlWsFlag.TEXT)
                    except Exception:
                        pass
                    continue
                if msg_type == 1 and msg.get("target") == "update":
                    for arg in msg.get("arguments", []):
                        if not isinstance(arg, dict):
                            continue
                        
                        # First check if full text is provided in messages[0].text
                        messages = arg.get("messages", [])
                        if messages and isinstance(messages, list) and isinstance(messages[0], dict):
                            msg_obj = messages[0]
                            server_text = msg_obj.get("text", "")
                            
                            if msg_obj.get("addToChainOfThought") or msg_obj.get("contentType") == "Thinking" or msg_obj.get("messageType") == "Progress":
                                title = msg_obj.get("title", "")
                                hidden = msg_obj.get("hiddenText", "")
                                
                                parts = []
                                if title and title not in parts:
                                    parts.append(title)
                                if server_text and server_text not in parts:
                                    parts.append(server_text)
                                if hidden and hidden not in parts:
                                    parts.append(hidden)
                                
                                combined = "\n".join(parts)
                                if combined and combined not in seen_thoughts:
                                    seen_thoughts.add(combined)
                                    is_started = True
                                    yield {"thought": combined + "\n\n"}
                                continue

                            msg_obj = arg.get("messages", [{}])[0]
                            
                            # Extract citations from sourceAttributions
                            source_attrs = msg_obj.get("sourceAttributions", [])
                            if source_attrs:
                                citations = {}
                                for i, attr in enumerate(source_attrs):
                                    url = attr.get("seeMoreUrl")
                                    name = attr.get("providerDisplayName", "")
                                    meta_str = attr.get("referenceMetadata", "{}")
                                    try:
                                        meta = json.loads(meta_str) if isinstance(meta_str, str) else {}
                                        ref_id = meta.get("citationRefId") or meta.get("referenceId")
                                        if not ref_id:
                                            ref_id = str(i + 1)
                                            # Bing often implicitly maps turnXsearchY to these if missing.
                                            # We'll map multiple possible implicit keys just in case.
                                            citations[str(i + 1)] = {"url": url, "name": name}
                                            citations[f"turn0search{i+1}"] = {"url": url, "name": name}
                                            citations[f"turn1search{i+1}"] = {"url": url, "name": name}
                                        if ref_id and url:
                                            citations[ref_id] = {"url": url, "name": name}
                                    except Exception:
                                        pass
                                if citations:
                                    yield {"citations": citations}

                            server_text = msg_obj.get("text", "")
                            if not server_text and "hiddenText" in msg_obj:
                                server_text = msg_obj["hiddenText"]

                            if server_text:
                                # Normalize \r\n to \n to prevent startswith() failures
                                server_text = server_text.replace("\r\n", "\n")
                                
                                if len(server_text) > len(seen_text):
                                    if server_text.startswith(seen_text):
                                        new_text = server_text[len(seen_text):]
                                    else:
                                        # Fallback: find longest common prefix in case of minor mismatches
                                        common_len = 0
                                        for i in range(min(len(seen_text), len(server_text))):
                                            if seen_text[i] == server_text[i]:
                                                common_len = i + 1
                                            else:
                                                break
                                        new_text = server_text[common_len:]
                                    
                                    seen_text = server_text
                                    is_started = True
                                    yield new_text
                                    continue

                        # Then check if a delta is provided via writeAtCursor
                        write_at_cursor = arg.get("writeAtCursor")
                        if write_at_cursor:
                            write_at_cursor = write_at_cursor.replace("\r\n", "\n")
                            seen_text += write_at_cursor
                            is_started = True
                            yield write_at_cursor
                elif msg_type == 2:
                    if msg.get("error"):
                        raise RuntimeError(f"Copilot E5 error: {msg['error']}")
                    return
                elif msg_type == 7:
                    return

        if not is_started:
            raise RuntimeError(f"Invalid response: {last_msg}")

    @staticmethod
    def _recv_frame(wss, deadline: float):
        """Block for one complete WS frame, or return ``None`` past ``deadline``.

        Reassembles libcurl's fragments like ``curl_cffi``'s own ``recv()`` but
        breaks out of the ``CURLE_AGAIN`` wait once ``deadline`` (epoch seconds)
        is reached, so an idle socket can't hang us indefinitely. Non-AGAIN curl
        errors (e.g. a closed connection) propagate to the caller.
        """
        sock_fd = wss.curl.getinfo(CurlInfo.ACTIVESOCKET)
        if sock_fd == _CURL_SOCKET_BAD:
            raise ConnectionError("WebSocket has no active socket")
        chunks = []
        while True:
            try:
                chunk, frame = wss.recv_fragment()
                chunks.append(chunk)
                if frame.bytesleft == 0 and frame.flags & CurlWsFlag.CONT == 0:
                    return b"".join(chunks)
            except CurlError as e:
                if e.code != CurlECode.AGAIN:
                    raise
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                select([sock_fd], [], [], min(0.5, remaining))

    @staticmethod
    def _solve_challenge(msg: dict):
        """Return the challenge-response token, or ``None`` if we can't solve it.

        Copilot's chat socket precedes the answer with a challenge frame. The
        proof-of-work variants (``hashcash``, ``copilot``) are computed in-process
        (:mod:`copilot.challenges`).
        """
        method = msg.get("method")
        parameter = msg.get("parameter")
        if method == "hashcash" and parameter:
            return solve_hashcash(parameter)
        if method == "copilot" and parameter:
            return solve_copilot_challenge(parameter)
        return None
