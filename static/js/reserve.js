document.addEventListener('DOMContentLoaded', function () {
    const timeBlocks = document.querySelectorAll('.time-slot-block:not(.booked)');
    const agreeCheckbox = document.getElementById('agree');
    const submitBtn = document.getElementById('submitBtn');
    const form = document.getElementById('reservationForm');

    function updateSubmitButtonState() {
        const checkedTimeCount = document.querySelectorAll('input[name="time_slot"]:checked').length;
        const hasTime = checkedTimeCount > 0;
        const hasAgreed = agreeCheckbox.checked;

        submitBtn.disabled = !(hasTime && hasAgreed);

        if (submitBtn.disabled) {
            submitBtn.title = "시간 선택과 약관 동의가 필요합니다";
            submitBtn.style.opacity = "0.6";
        } else {
            submitBtn.title = "";
            submitBtn.style.opacity = "1";
        }
    }

    function updateTimelineUI() {
        const checkedBoxes = Array.from(document.querySelectorAll('input[name="time_slot"]:checked'))
            .sort((a, b) => parseInt(a.value) - parseInt(b.value));

        document.querySelectorAll('.time-slot-block').forEach(b => b.classList.remove('selected'));
        checkedBoxes.forEach(input => {
            input.closest('.time-slot-block').classList.add('selected');
        });

        updateSubmitButtonState();
    }

    timeBlocks.forEach(block => {
        block.addEventListener('click', function (e) {
            const checkbox = this.querySelector('input');
            if (checkbox.disabled) return;

            checkbox.checked = !checkbox.checked;

            const checkedBoxes = Array.from(document.querySelectorAll('input[name="time_slot"]:checked'))
                .sort((a, b) => parseInt(a.value) - parseInt(b.value));

            if (checkedBoxes.length > 2) {
                alert("예약은 하루 최대 2시간(2개 슬롯)까지만 가능합니다.");
                checkbox.checked = false;
            } else if (checkedBoxes.length === 2) {
                const val1 = parseInt(checkedBoxes[0].value);
                const val2 = parseInt(checkedBoxes[1].value);
                if (val2 - val1 !== 1) {
                    alert("이용 시간은 반드시 연속된 시간으로만 선택 가능합니다.");
                    checkbox.checked = false;
                }
            }

            updateTimelineUI();
        });
    });

    if (agreeCheckbox) {
        agreeCheckbox.addEventListener('change', updateSubmitButtonState);
    }

    if (form) {
        form.addEventListener('submit', function (e) {
            const checkedTimeCount = document.querySelectorAll('input[name="time_slot"]:checked').length;
            if (checkedTimeCount === 0) {
                e.preventDefault();
                alert("이용 시간을 최소 1시간 이상 선택해주세요.");
                return;
            }

            const participants = ['elementary', 'middle', 'high', 'teen', 'adult']
                .map(name => parseInt(document.querySelector(`input[name="${name}"]`).value) || 0);
            const total = participants.reduce((a, b) => a + b, 0);
            if (total === 0) {
                e.preventDefault();
                alert("이용 인원은 총합 최소 1명 이상이어야 합니다.");
            }
        });
    }

    updateSubmitButtonState();
    updateTimelineUI();
});