function escapeHtml(text) {
  return text.replace(/[&<>"']/g, function (m) {
    return {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;'
    }[m];
  });
}

function appendMessage(sender, message, isHTML = false) {
  const chatbox = document.getElementById("chatbox");
  const p = document.createElement("p");
  p.innerHTML = `<strong>${sender}:</strong> ${isHTML ? message : escapeHtml(message)}`;
  chatbox.appendChild(p);
  chatbox.scrollTo({ top: chatbox.scrollHeight, behavior: "smooth" });
}

async function sendMessage(customMessage = null) {
  const inputBox = document.getElementById("user-input");
  const input = customMessage || inputBox.value.trim();
  if (!input) return;

  appendMessage("You", input);
  inputBox.value = "";
  inputBox.focus();

  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: input })
    });
    const data = await response.json();
    appendMessage("Bot", data.response, true);
  } catch (error) {
    appendMessage("Bot", "⚠️ Error communicating with server.", false);
  }
}

// Press Enter to send message
document.getElementById("user-input").addEventListener("keydown", function (event) {
  if (event.key === "Enter") {
    event.preventDefault();
    sendMessage();
  }
});

// Bootstrap modal instance
const seatModalEl = document.getElementById("seatModal");
const seatModal = new bootstrap.Modal(seatModalEl);

// Generate seat buttons and show modal
function showSeatModal(bookedSeats = []) {
  const container = document.getElementById("seatsContainer");
  container.innerHTML = "";

  for (let i = 1; i <= 10; i++) {
    const seatId = `seat_${i}`;
    const btn = document.createElement("button");
    btn.textContent = i;
    btn.classList.add("seat", "btn", "btn-outline-primary");

    if (bookedSeats.includes(seatId)) {
      btn.classList.add("booked", "btn-secondary");
      btn.disabled = true;
    }

    btn.onclick = () => {
      seatModal.hide();
      sendMessage(seatId); // Send seat ID as message
    };

    container.appendChild(btn);
  }

  seatModal.show();
}

// Detect when bot asks to choose seat and trigger modal
const originalAppendMessage = appendMessage;
appendMessage = function (sender, message, isHTML = false) {
  originalAppendMessage(sender, message, isHTML);

  if (
    sender === "Bot" &&
    /choose your seat|seat number/i.test(message)
  ) {
    fetch("/get_booked_seats", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    })
      .then((res) => res.json())
      .then((data) => {
        showSeatModal(data.bookedSeats || []);
      })
      .catch(() => {
        showSeatModal([]);
      });
  }
};
