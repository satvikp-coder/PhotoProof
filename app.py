"""Local Streamlit interface for the PhotoProof research prototype."""

from __future__ import annotations

import io

import streamlit as st
from PIL import Image, UnidentifiedImageError

from photoproof.forensics import error_level_analysis, noise_residual

st.set_page_config(
    page_title="PhotoProof | Image Forensics",
    page_icon="🔎",
    layout="wide",
)

st.title("PhotoProof")
st.subheader("Image Forgery Detection and Localization")
st.write(
    "Explore visual signals that may help an investigator inspect an image. "
    "This prototype does not make a definitive real/fake decision."
)

with st.expander("Important limitations", expanded=True):
    st.warning(
        "ELA and noise-residual maps can highlight normal compression, texture, "
        "sharpening, resizing, and camera-processing artifacts. A highlighted "
        "region is not proof of forgery, and an unremarkable map does not prove authenticity."
    )

uploaded_file = st.file_uploader(
    "Choose an image to analyse",
    type=["jpg", "jpeg", "png", "webp", "bmp"],
    help="For privacy, this starter app is intended to run locally on your computer.",
)

if uploaded_file is not None:
    try:
        raw_bytes = uploaded_file.getvalue()
        with Image.open(io.BytesIO(raw_bytes)) as opened_image:
            original = opened_image.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError):
        st.error("The selected file could not be opened as a supported image.")
    else:
        st.caption(
            f"File: {uploaded_file.name} · Dimensions: {original.width} × {original.height} pixels"
        )
        original_col, ela_col = st.columns(2)
        with original_col:
            st.markdown("#### Original image")
            st.image(original, use_container_width=True)
        with ela_col:
            st.markdown("#### ELA visualization")
            ela_quality = st.slider(
                "JPEG re-encoding quality",
                min_value=50,
                max_value=100,
                value=90,
                step=5,
                help="Changing this setting changes the visualization; it is not a calibrated fraud score.",
            )
            ela = error_level_analysis(original, quality=ela_quality, scale=10)
            st.image(ela, use_container_width=True)
            st.caption("Amplified recompression differences; inspect cautiously.")

        st.markdown("#### Noise-residual visualization")
        residual = noise_residual(original, amplification=4.0)
        st.image(residual, use_container_width=True)
        st.caption(
            "High-frequency differences from a smoothed image. Texture and processing "
            "can create differences even in authentic photographs."
        )

        with st.expander("What these results mean"):
            st.write(
                "These images are exploratory forensic visualizations, not a probability "
                "that the image is fake. The next project stage is to add copy-move "
                "analysis and evaluate localization against labelled examples."
            )
else:
    st.info("Upload an image to begin. No image is analysed until you select a file.")

st.divider()
st.caption("PhotoProof · Educational prototype · Do not use as sole evidence of authenticity.")
