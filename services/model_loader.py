import os

from transformers import pipeline

from services.logger_services import logger

MODEL_NAME = "cardiffnlp/twitter-roberta-base-sentiment-latest"
MODEL_CACHE_DIR = "./models/sentiment_model"

sentiment_model = None


def _is_model_cached() -> bool:
    return os.path.isfile(os.path.join(MODEL_CACHE_DIR, "config.json"))


def load_sentiment_model():
    """Load model from local cache, download only if not present."""
    global sentiment_model

    if sentiment_model is not None:
        return sentiment_model

    if _is_model_cached():
        logger.info("model_loader: loading sentiment model from local cache...")
        print("✅ Loading model from local cache...")
        sentiment_model = pipeline(
            "sentiment-analysis",
            model=MODEL_CACHE_DIR,
        )
    else:
        logger.info("model_loader: downloading sentiment model for the first time...")
        print("⬇️ Downloading model for the first time...")
        os.makedirs(MODEL_CACHE_DIR, exist_ok=True)
        sentiment_model = pipeline(
            "sentiment-analysis",
            model=MODEL_NAME,
        )
        sentiment_model.model.save_pretrained(MODEL_CACHE_DIR)
        sentiment_model.tokenizer.save_pretrained(MODEL_CACHE_DIR)
        logger.info(f"model_loader: model saved to {MODEL_CACHE_DIR}")
        print("✅ Model saved locally!")

    return sentiment_model
