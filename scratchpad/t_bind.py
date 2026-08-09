import os
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
load_dotenv(".env")
for style in ("bind", "ctor", "plain"):
    try:
        if style == "bind":
            llm = ChatGoogleGenerativeAI(model="gemini-3.5-flash-lite", temperature=0,
                                         api_key=os.environ["GOOGLE_API_KEY"]
                                         ).bind(response_mime_type="application/json")
        elif style == "ctor":
            llm = ChatGoogleGenerativeAI(model="gemini-3.5-flash-lite", temperature=0,
                                         api_key=os.environ["GOOGLE_API_KEY"],
                                         response_mime_type="application/json")
        else:
            llm = ChatGoogleGenerativeAI(model="gemini-3.5-flash-lite", temperature=0,
                                         api_key=os.environ["GOOGLE_API_KEY"])
        r = llm.invoke([("system", 'Svar KUN med JSON: {"ok": true, "reason": "..."}'),
                        ("human", "Kan jeg få kørselsfradrag?")])
        print(f"{style:6} OK  -> {repr(r.content)[:120]}")
    except Exception as e:
        print(f"{style:6} FAIL {type(e).__name__}: {str(e)[:150]}")
