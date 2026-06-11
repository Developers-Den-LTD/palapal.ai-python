import uvicorn 
from fastapi import FastAPI
from routes import home_route, url_finder_route, scrapper, AI_visibility

application = FastAPI(title="Palapalai API")

application.include_router(home_route.router)
application.include_router(url_finder_route.router)
application.include_router(scrapper.router)
application.include_router(AI_visibility.router)



if __name__ == "__main__":
    uvicorn.run("application:application", host="0.0.0.0", port=8000, reload=True)


