from fastapi.staticfiles import StaticFiles
import uvicorn
from fastapi import FastAPI, Depends
from routes import home_route, url_finder_route, scrapper, AI_visibility, Review_Velocity, technical_foundation, ddi_score_route
from routes import pending_responses, all_responses, logs_router, Action_Cards_route, llms_txt_generator_route, Review_Reply, socialmedia_scrape_route, ddi_batch_route
from routes import competitor_analysis_route, page_audit_route, reddit_discussion_route
from services.model_loader import load_sentiment_model
from utils.auth_utils import verify_secret_key

# Load model ONCE when app starts
load_sentiment_model()

application = FastAPI(title="Palapalai API", prefix="/api")


application.include_router(home_route.router)
application.include_router(url_finder_route.router, dependencies=[Depends(verify_secret_key)])
application.include_router(scrapper.router, dependencies=[Depends(verify_secret_key)])
application.include_router(AI_visibility.router, dependencies=[Depends(verify_secret_key)])
application.include_router(Review_Velocity.router, dependencies=[Depends(verify_secret_key)])
application.include_router(technical_foundation.router, dependencies=[Depends(verify_secret_key)])
application.include_router(ddi_score_route.router, dependencies=[Depends(verify_secret_key)])
application.include_router(ddi_batch_route.router, dependencies=[Depends(verify_secret_key)])
application.include_router(pending_responses.router, dependencies=[Depends(verify_secret_key)])
application.include_router(all_responses.router, dependencies=[Depends(verify_secret_key)])
application.include_router(logs_router.router, dependencies=[Depends(verify_secret_key)])
application.include_router(Action_Cards_route.router, dependencies=[Depends(verify_secret_key)])
application.include_router(llms_txt_generator_route.router, dependencies=[Depends(verify_secret_key)])
application.include_router(Review_Reply.router, dependencies=[Depends(verify_secret_key)])
application.include_router(socialmedia_scrape_route.router, dependencies=[Depends(verify_secret_key)])
application.include_router(competitor_analysis_route.router, dependencies=[Depends(verify_secret_key)])
application.include_router(page_audit_route.router, dependencies=[Depends(verify_secret_key)])
application.include_router(reddit_discussion_route.router, dependencies=[Depends(verify_secret_key)])

# application.mount("/images", StaticFiles(directory="images"), name="images")

if __name__ == "__main__":
    uvicorn.run("application:application", host="0.0.0.0", port=8000, reload=True)
