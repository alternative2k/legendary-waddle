import streamlit as st
import boto3
import av
import time
import uuid
from botocore.exceptions import NoCredentialsError, ClientError
from streamlit_webrtc import webrtc_streamer, RTCConfiguration, WebRtcMode

# Streamlit secrets (set in Cloud app settings)
try:
    AWS_ACCESS_KEY_ID = st.secrets["AWS_ACCESS_KEY_ID"]
    AWS_SECRET_ACCESS_KEY = st.secrets["AWS_SECRET_ACCESS_KEY"]
    AWS_REGION = st.secrets.get("AWS_REGION", "us-east-1")
    S3_BUCKET = st.secrets["S3_BUCKET"]
except KeyError:
    st.error("❌ **Missing AWS secrets**. Add to Streamlit Cloud settings.")
    st.stop()

st.title("🎥 Webcam Recorder → S3")
st.markdown("**Record video → Auto-upload to S3**")

# Session state for recording management
if "recording" not in st.session_state:
    st.session_state.recording = False
if "recorded_video" not in st.session_state:
    st.session_state.recorded_video = None
if "status" not in st.session_state:
    st.session_state.status = "Ready"
if "start_time" not in st.session_state:
    st.session_state.start_time = None

# S3 client
@st.cache_resource
def get_s3_client():
    return boto3.client(
        's3',
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=AWS_REGION
    )

# Video processor (passthrough)
def video_frame_callback(frame: av.VideoFrame) -> av.VideoFrame:
    if st.session_state.recording:
        # Store frames during recording
        if "frames" not in st.session_state:
            st.session_state.frames = []
        st.session_state.frames.append(frame)
    return frame

def upload_to_s3(video_bytes):
    try:
        s3 = get_s3_client()
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        file_key = f"recordings/webcam-{timestamp}-{uuid.uuid4().hex[:8]}.mkv"
        
        progress_bar = st.progress(0)
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=file_key,
            Body=video_bytes,
            ContentType="video/x-matroska"
        )
        
        s3_url = f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{file_key}"
        file_size = len(video_bytes) / (1024*1024)
        progress_bar.progress(100)
        st.session_state.status = f"✅ Uploaded! ({file_size:.1f}MB)"
        st.success(f"**Video saved**: [{file_key}]({s3_url})")
        st.balloons()
        
    except NoCredentialsError:
        st.error("❌ **AWS credentials invalid**. Check Streamlit secrets.")
    except ClientError as e:
        st.error(f"❌ **S3 Error**: {e}. Verify bucket `{S3_BUCKET}` exists + IAM permissions.")
    except Exception as e:
        st.error(f"❌ **Upload failed**: {str(e)}")

# WebRTC configuration
rtc_config = RTCConfiguration(
    iceServers=[{"urls": ["stun:stun.l.google.com:19302"]}]
)

# Main recording component - MINIMAL PARAMETERS ONLY
webrtc_ctx = webrtc_streamer(
    key="recorder",
    mode=WebRtcMode.SENDONLY,
    rtc_configuration=rtc_config,
    video_frame_callback=video_frame_callback,
    media_stream_constraints={
        "video": {
            "width": {"ideal": 1280, "max": 1920},
            "height": {"ideal": 720, "max": 1080},
            "frameRate": {"ideal": 30, "max": 30}
        }
    },
    # NO video_recorder_class or on_stop - using session state control
)

# Control buttons and status
col1, col2 = st.columns([3, 1])
with col1:
    if st.session_state.status == "Recording":
        elapsed = time.time() - st.session_state.start_time
        st.metric("⏱️", f"{int(elapsed//60):02d}:{int(elapsed%60):02d}")
    else:
        st.metric("Status", st.session_state.status)

with col2:
    if webrtc_ctx.state.playing and not st.session_state.recording:
        if st.button("🔴 **Start Recording**", use_container_width=True, type="primary"):
            st.session_state.recording = True
            st.session_state.start_time = time.time()
            st.session_state.status = "Recording"
            st.session_state.frames = []
            st.rerun()
    elif st.session_state.recording:
        if st.button("⏹️ **Stop & Upload**", use_container_width=True, type="secondary"):
            st.session_state.recording = False
            st.session_state.status = "Processing..."
            
            # Convert frames to video bytes (simplified - use av container)
            if "frames" in st.session_state and st.session_state.frames:
                container = av.open(io.BytesIO(), mode='w', format='matroska')
                stream = container.add_stream('libx264', rate=30)
                stream.width = 1280
                stream.height = 720
                stream.pix_fmt = 'yuv420p'
                
                for frame in st.session_state.frames:
                    frame_converted = frame.reformat(width=1280, height=720)
                    packet = stream.encode(frame_converted)
                    if packet:
                        container.mux(packet)
                
                container.close()
                video_bytes = container.output_bytes
                st.session_state.recorded_video = video_bytes
                upload_to_s3(video_bytes)
                del st.session_state.frames
            
            st.rerun()

# Reset button
if st.button("📹 **New Recording**"):
    for key in ["recording", "recorded_video", "status", "start_time", "frames"]:
        if key in st.session_state:
            del st.session_state[key]
    st.rerun()

# Footer
st.markdown("---")
st.info("""
**ℹ️ Setup**:
- Add AWS secrets in Streamlit Cloud settings
- IAM policy: `s3:PutObject` for bucket `your-bucket-name`
- Works on all browsers (HTTPS required)
""")
