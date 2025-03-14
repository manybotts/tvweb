document.addEventListener('DOMContentLoaded', function() {

	// Initialize slideshow
	let slideIndex = 0;
    showSlides(slideIndex);

    function plusSlides(n) {
        showSlides(slideIndex += n);
    }

    function currentSlide(n) {
        showSlides(slideIndex = n - 1);
    }

	function showSlides(n) {
		let i;
		let slides = document.getElementsByClassName("mySlides");
        let dots = document.getElementsByClassName("dot");
        let slideshowContainer = document.querySelector(".slideshow-container");

		if (n >= slides.length) { slideIndex = 0; }
		if (n < 0) { slideIndex = slides.length - 1; }

        // Remove 'active-slide' class from all slides
        for (let i = 0; i < slides.length; i++) {
            slides[i].classList.remove("active-slide");
        }

         if (slides.length > 0) {
             slides[slideIndex].classList.add("active-slide");
             // Update blurred background image
             let imgSrc = slides[slideIndex].querySelector('img').src;
              if (slideshowContainer) {
                slideshowContainer.style.backgroundImage = `url(${imgSrc})`;
              }
          }

        // Ensure that dots exist before trying to access/modify them.
        for (i = 0; i < dots.length; i++) {
          dots[i].className = dots[i].className.replace(" active", "");
           }
         // Check if dots exist before trying to access them
        if (dots.length > 0) {
             dots[slideIndex].className += " active";
         }
	}

    // Automatic slideshow advance
    let slideInterval = setInterval(() => { plusSlides(1); }, 5000);
     // Pause slideshow on hover
    const slideshowContainer = document.querySelector(".slideshow-container");

    if(slideshowContainer){
        slideshowContainer.addEventListener('mouseover', () => {
            clearInterval(slideInterval);
        });

        slideshowContainer.addEventListener('mouseout', () => {
            slideInterval = setInterval(() => { plusSlides(1); }, 5000);
        });
    }
});
