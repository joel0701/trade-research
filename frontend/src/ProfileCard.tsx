/** Bottom-left profile card (photo, name, email); opens Settings. */
import { useNavigate } from "react-router-dom";
import { Avatar, useSettings } from "./settings";

export default function ProfileCard() {
  const { profile } = useSettings();
  const navigate = useNavigate();
  const empty = !profile.name && !profile.email;
  return (
    <button className="profile-card" onClick={() => navigate("/settings")} title="Profile and settings">
      <Avatar size={38} />
      <span className="profile-text">
        <strong>{profile.name || "Set up your profile"}</strong>
        <span className="muted">{profile.email || (empty ? "Name, photo, time zone" : "")}</span>
        <span className="plan">Local</span>
      </span>
    </button>
  );
}
