import cv2
import mediapipe as mp
import numpy as np
import streamlit as st
import streamlit_shadcn_ui as ui

# Set up the page config
st.set_page_config(
    page_title="Curl Counter",
    page_icon="💪",
    layout="wide"
)
left, right = st.columns(2)

if 'checkbox_state' not in st.session_state:
    st.session_state['checkbox_state'] = False

def toggle_checkbox():
    st.session_state['checkbox_state'] = not st.session_state['checkbox_state']


footer="""<style>
a:link , a:visited{
color: blue;
background-color: transparent;
text-decoration: underline;
}

a:hover,  a:active {
color: red;
background-color: transparent;
text-decoration: underline;
}

.footer {
position: fixed;
left: 2;
bottom: 0;
width: 100%;
background-color: transparent;
color: white;
text-align: left;
}
</style>
<div class="footer">
<p>Made with ❤ by Team Brackets</p>
</div>
"""
st.markdown(footer,unsafe_allow_html=True)
page_bg_img = """
<style>
[data-testid="stAppViewBlockContainer"] {
background-image: url("https://i.imgur.com/5zpQXW7.png");
background-size: cover;
}

[data-testid="stHeader"] {
background-color: rgba(0,0,0,0);
}


</style>

"""
st.markdown(page_bg_img, unsafe_allow_html=True)
element1 = """
[data-testid="element-container"] {
background-color: rgba(0,0,0,0);
}

"""
with right:
    
    run = st.button("Toggle Tool", on_click=toggle_checkbox, type="primary")
    goodtime_placeholder = st.empty()
    badtime_placeholder = st.empty()

with left:

    "# Curl Counter"
    with ui.element("div", className="flex gap-2", key="buttons_group1"):
        ui.element("link_button", text="Diagnosia | Home", url="https://diagnosia.netlify.app", variant="outline", key="btn1")

    """Curl Counter is a computer vision tool that can count bicep curls during exercises."""

    # Choose experience level

# MediaPipe and OpenCV setup
mp_drawing = mp.solutions.drawing_utils
mp_pose = mp.solutions.pose

# Function to calculate the angle
def calculate_angle(a, b, c):
    a = np.array(a)  # First
    b = np.array(b)  # Mid
    c = np.array(c)  # End
    
    radians = np.arctan2(c[1]-b[1], c[0]-b[0]) - np.arctan2(a[1]-b[1], a[0]-b[0])
    angle = np.abs(radians*180.0/np.pi)
    
    if angle > 180.0:
        angle = 360-angle
        
    return angle

# Set up webcam feed
cap = cv2.VideoCapture(0)

# Curl counter variables
counter = 0
stage = None

# Streamlit UI

with left:
    
    frame_window = st.image([])

    if run:
        with mp_pose.Pose(min_detection_confidence=0.5, min_tracking_confidence=0.5) as pose:
            while st.session_state['checkbox_state']:
                ret, frame = cap.read()
                if not ret:
                    break


                # Recolor image to RGB
                image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image.flags.writeable = False
            
                # Make detection
                results = pose.process(image)
            
                # Recolor back to BGR
                image.flags.writeable = True
                image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
                
                # Extract landmarks
                try:
                    landmarks = results.pose_landmarks.landmark
                    
                    # Get coordinates
                    shoulder = [landmarks[mp_pose.PoseLandmark.LEFT_SHOULDER.value].x,landmarks[mp_pose.PoseLandmark.LEFT_SHOULDER.value].y]
                    elbow = [landmarks[mp_pose.PoseLandmark.LEFT_ELBOW.value].x,landmarks[mp_pose.PoseLandmark.LEFT_ELBOW.value].y]
                    wrist = [landmarks[mp_pose.PoseLandmark.LEFT_WRIST.value].x,landmarks[mp_pose.PoseLandmark.LEFT_WRIST.value].y]
                    
                    # Calculate angle
                    angle = calculate_angle(shoulder, elbow, wrist)
                    
                    # Visualize angle
                    cv2.putText(image, str(angle), 
                                tuple(np.multiply(elbow, [640, 480]).astype(int)), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2, cv2.LINE_AA
                                        )
                    
                    # Curl counter logic
                    if angle > 160:
                        stage = "down"
                    if angle < 30 and stage =='down':
                        stage="up"
                        counter +=1
                        print(counter)
                            
                except:
                    pass
                
                # Render curl counter
                # Setup status box
                cv2.rectangle(image, (0,0), (225,73), (245,117,16), -1)
                
                # Rep data
                cv2.putText(image, 'REPS', (15,12), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,0), 1, cv2.LINE_AA)
                cv2.putText(image, str(counter), 
                            (10,60), 
                            cv2.FONT_HERSHEY_SIMPLEX, 2, (255,255,255), 2, cv2.LINE_AA)
                
                # Stage data
                cv2.putText(image, 'STAGE', (65,12), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,0), 1, cv2.LINE_AA)
                cv2.putText(image, stage, 
                            (60,60), 
                            cv2.FONT_HERSHEY_SIMPLEX, 2, (255,255,255), 2, cv2.LINE_AA)
                
                
                # Render detections
                mp_drawing.draw_landmarks(image, results.pose_landmarks, mp_pose.POSE_CONNECTIONS,
                                        mp_drawing.DrawingSpec(color=(245,117,66), thickness=2, circle_radius=2), 
                                        mp_drawing.DrawingSpec(color=(245,66,230), thickness=2, circle_radius=2) 
                                        )

                # Display the image
                frame_window.image(image)

                with right:
                    goodtime_placeholder.text(f"Reps Done: {counter}") 

cap.release()

