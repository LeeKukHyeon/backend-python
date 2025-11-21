ACTION_HANDLERS = {}

def register_action(name):
    "액션 등록 데코레이터"

    def decorator(func):
        ACTION_HANDLERS[name] = func
        return func
    return decorator