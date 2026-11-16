"""Request -> service -> response; không có logic giao diện."""
from Error import AppError, AuthError, SessionError
from GLP import DEFAULT_SESSION, HOLDING_SESSION, Request, Response, error_response
from .chat import packed

ARG_COUNTS = {
    "SYS.PING": (0,),
    "AUTH.LOGIN": (2,), "AUTH.REGISTER": (2,), "AUTH.LOGOUT": (0,),
    "CHAT.LIST": (0,), "CHAT.SEND": (2,), "CHAT.HISTORY": (1, 2, 3),
}


def reply(action, *args, session_update=HOLDING_SESSION):
    return Response(action, session_update, tuple(
        arg if isinstance(arg, bytes) else str(arg).encode("utf-8") for arg in args
    ))


def dispatch(request, auth, connection, ip_addr, online, message, *, peer_port=None):
    if not isinstance(request, Request):
        return error_response("ERROR.CLIENT", "Server chỉ nhận Request")
    try:
        route, args = request.route, request.args
        if route.startswith("FILE."):
            session = auth.get_session(request.session_id, connection)
            return message.files.dispatch(request, session, connection, online)
        if route not in ARG_COUNTS or len(args) not in ARG_COUNTS[route]:
            raise AppError("Route hoặc số args không đúng")
        if route == "SYS.PING":
            return reply("OK.PONG")
        if route in ("AUTH.LOGIN", "AUTH.REGISTER"):
            method = auth.login if route == "AUTH.LOGIN" else auth.register
            session = method(*args, ip_addr=ip_addr, connection_id=connection, peer_port=peer_port)
            online.add(connection, session.user_id, ip_addr)
            user = auth.db.execute(
                "SELECT user_id,username,dispname FROM users WHERE user_id=?", (session.user_id,)
            ).fetchone()
            return reply("AUTH.OK", packed(dict(user)), session_update=session.token)
        session = auth.get_session(request.session_id, connection)
        if route == "AUTH.LOGOUT":
            if hasattr(message, "files"):
                message.files.disconnect(connection)
            auth.logout(request.session_id, connection)
            online.remove(connection)
            return reply("AUTH.BYE", session_update=DEFAULT_SESSION)
        if route == "CHAT.LIST":
            return reply("CHAT.ITEMS", packed(message.chats(session.user_id, online)))
        if route == "CHAT.HISTORY":
            target = message.target(args[0])
            limit = int(args[1]) if len(args) > 1 else 10
            before = int(args[2]) if len(args) > 2 else 0
            rows, cursor = message.history(session.user_id, target, limit, before)
            return reply("CHAT.PAGE", args[0], packed(rows), cursor)
        if route == "CHAT.SEND":
            target = message.target(args[0])
            record = message.send(session.user_id, target, args[1])
            event = reply("CHAT.MESSAGE", packed(record))
            for peer, uid in list(online.conns.items()):
                if peer is not connection and (target is None or uid in (session.user_id, target)):
                    try:
                        peer.send(event)
                    except OSError:
                        pass
            return reply("CHAT.SENT", packed(record))
        raise AppError("Route chưa được xử lý")
    except SessionError as error:
        if hasattr(message, "files"):
            message.files.disconnect(connection)
        auth.disconnect(connection)
        online.remove(connection)
        return error_response("ERROR.AUTH", str(error), session_update=DEFAULT_SESSION)
    except AuthError as error:
        return error_response("ERROR.AUTH", str(error))
    except AppError as error:
        return error_response("ERROR.CLIENT", str(error))
    except (ValueError, OverflowError):
        return error_response("ERROR.CLIENT", "Tham số không hợp lệ")
