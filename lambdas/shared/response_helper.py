def success(data):
    return {"status": "ok", "data": data}


def error(message, details=None):
    response = {"status": "error", "message": message}
    if details is not None:
        response["details"] = details
    return response
