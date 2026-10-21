from typing import Annotated

from pydantic import AfterValidator, StringConstraints


def nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("Message content must not be blank")
    return value


MessageText = Annotated[str, StringConstraints(min_length=1, max_length=20000), AfterValidator(nonblank)]
