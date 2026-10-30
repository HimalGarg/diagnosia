import streamlit as st
import numpy as np
import matplotlib.pyplot as plt
import cv2
import json
from tensorflow.keras.models import load_model
from tensorflow.keras.layers import DepthwiseConv2D
from tensorflow.keras.utils import load_img, img_to_array
import base64

st.set_page_config(
    page_title="Skin Cancer",
    page_icon=":world_map:️",
    layout="wide",
)
IMAGE_SIZE = 224

def get_base64_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode()

bg_image_base64 = get_base64_image("bg.png")

page_bg_img = f"""
<style>
[data-testid="stAppViewBlockContainer"] {{
    background-image: url("data:image/png;base64,{bg_image_base64}");
    background-size: cover;
}}

[data-testid="stHeader"] {{
    background-color: rgba(0,0,0,0);
}}
</style>
"""
st.markdown(page_bg_img, unsafe_allow_html=True)

element1 = """
[data-testid="element-container"] {
    background-color: rgba(0,0,0,0);
}
"""

class CustomDepthwiseConv2D(DepthwiseConv2D):
    def __init__(self, *args, **kwargs):
        if 'groups' in kwargs:
            del kwargs['groups']
        super(CustomDepthwiseConv2D, self).__init__(*args, **kwargs)

skin_cancer_model = load_model('./skin_cancer_classifier.h5')
custom_objects = {'DepthwiseConv2D': CustomDepthwiseConv2D}
full_model = load_model('full_model.h5', custom_objects=custom_objects)

with open('class_indices.json', 'r') as json_file:
    class_indices = json.load(json_file)

cancer_descriptions = {
    'akiec': "Actinic keratoses and intraepithelial carcinoma / Bowen's disease",
    'bcc': 'Basal cell carcinoma',
    'bkl': 'Benign keratosis-like lesions (solar lentigines / seborrheic keratoses and lichen-planus like keratoses)',
    'df': 'Dermatofibroma',
    'mel': 'Melanoma',
    'nv': 'Melanocytic nevi',
    'vasc': 'Vascular lesions (angiomas, angiokeratomas, pyogenic granulomas, and hemorrhage)',
    'healthy': 'This is healthy skin'
}

def generate_grid_image(image_array, grid_size=30):
    grid = np.zeros((grid_size, grid_size))
    image_shape = image_array.shape[:2]
    step_x = image_shape[0] // grid_size
    step_y = image_shape[1] // grid_size

    for i in range(grid_size):
        for j in range(grid_size):
            grid[i, j] = np.mean(image_array[i*step_x:(i+1)*step_x, j*step_y:(j+1)*step_y])

    return grid

def generate_numerical_grid_image(image_array, threshold=0.5, grid_size=30):
    grid = np.zeros((grid_size, grid_size))
    image_shape = image_array.shape[:2]
    step_x = image_shape[0] // grid_size
    step_y = image_shape[1] // grid_size

    for i in range(grid_size):
        for j in range(grid_size):
            mean_value = np.mean(image_array[i*step_x:(i+1)*step_x, j*step_y:(j+1)*step_y])
            grid[i, j] = 1 if mean_value >= threshold else 0

    return grid

def generate_bounding_box(image_array, threshold=0.5):
    img = cv2.cvtColor(image_array, cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        confidence = 1 if np.mean(image_array[y:y+h, x:x+w]) >= threshold else 0
        cv2.rectangle(img, (x, y), (x + w, y + h), (0, 255 * confidence, 0), 2)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

def generate_confidence_grid(predictions, grid_size):
    confidence_grid = np.zeros((grid_size, grid_size))
    for i in range(grid_size):
        for j in range(grid_size):
            confidence_grid[i, j] = predictions[0, i * grid_size + j] if i * grid_size + j < predictions.shape[1] else 0
    return confidence_grid

def predict_cancer(image_path):
    img = load_img(image_path, target_size=(150, 150))
    img_array = img_to_array(img) / 255.0
    img_array = np.expand_dims(img_array, axis=0)

    prediction = skin_cancer_model.predict(img_array)
    if prediction[0] > 0.5:
        cancer_detected = True
        cancer_label = 'Skin Cancer'
    else:
        cancer_detected = False
        cancer_label = 'Healthy Skin'

    if not cancer_detected:
        return cancer_label, None, None, None, None, None, None

    img_full = load_img(image_path, target_size=(IMAGE_SIZE, IMAGE_SIZE))
    img_array_full = img_to_array(img_full) / 255.0
    img_array_full = np.expand_dims(img_array_full, axis=0)

    predictions = full_model.predict(img_array_full)
    predicted_index = np.argmax(predictions, axis=1)[0]
    predicted_label = list(class_indices.keys())[predicted_index]
    confidence = predictions[0][predicted_index]

    if predicted_label == 'healthy':
        return predicted_label, confidence, None, None, None, None, img_array_full

    img_cv2 = cv2.imread(image_path)
    img_cv2 = cv2.resize(img_cv2, (IMAGE_SIZE, IMAGE_SIZE))

    grid_image = generate_grid_image(img_array_full[0])
    numerical_grid_image = generate_numerical_grid_image(img_array_full[0])
    bounding_box_image = generate_bounding_box(img_cv2)
    confidence_grid = generate_confidence_grid(predictions, grid_size=numerical_grid_image.shape[0])

    return predicted_label, confidence, grid_image, numerical_grid_image, bounding_box_image, confidence_grid, img_array_full

st.title("Skin Cancer Detection")

uploaded_file = st.file_uploader("Choose an image...", type=["jpg", "jpeg", "png"])
if uploaded_file is not None:
    st.image(uploaded_file, caption='Uploaded Image.', use_column_width=True)
    st.write("")
    st.write("Classifying...")

    with open('temp_image.jpg', 'wb') as f:
        f.write(uploaded_file.getbuffer())

    predicted_label, confidence, grid_image, numerical_grid_image, bounding_box_image, confidence_grid, original_image_array = predict_cancer('temp_image.jpg')

    st.write(f"Predicted result: {predicted_label}")
    if predicted_label != 'Healthy Skin':
        st.write(f"Confidence: {confidence:.2f}")
        st.write(f"Description: {cancer_descriptions[predicted_label]}")

        fig, axs = plt.subplots(1, 4, figsize=(20, 20))

        axs[0].imshow(grid_image, cmap='hot', interpolation='nearest')
        axs[0].set_title('30x30 Grid with Markings')

        axs[1].imshow(numerical_grid_image, cmap='binary', interpolation='nearest')
        axs[1].set_title('30x30 Numerical Grid')

        axs[2].imshow(bounding_box_image)
        axs[2].set_title('Bounding Box Image')

        axs[3].imshow(confidence_grid, cmap='hot', interpolation='nearest')
        axs[3].set_title('Confidence Grid')

        st.pyplot(fig)
