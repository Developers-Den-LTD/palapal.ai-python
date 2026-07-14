import uvicorn
from fastapi import FastAPI
from routes import home_route, url_finder_route, scrapper, AI_visibility, Review_Velocity, technical_foundation, ddi_score_route, pending_responses, all_responses, logs_router, Action_Cards_route
from services.model_loader import load_sentiment_model

# Load model ONCE when app starts
load_sentiment_model()

application = FastAPI(title="Palapalai API")

application.include_router(home_route.router)
application.include_router(url_finder_route.router)
application.include_router(scrapper.router)
application.include_router(AI_visibility.router)
application.include_router(Review_Velocity.router)
application.include_router(technical_foundation.router)
application.include_router(ddi_score_route.router)
application.include_router(pending_responses.router)
application.include_router(all_responses.router)
application.include_router(logs_router.router)
application.include_router(Action_Cards_route.router)


if __name__ == "__main__":
    uvicorn.run("application:application", host="0.0.0.0", port=8000, reload=True)
